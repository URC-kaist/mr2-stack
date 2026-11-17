// Explicit single-motor hardware test. Never launched by a rover launch file.
#include "mr2_can_bus_core/can_device.hpp"
#include "mr2_devices_robstride/protocol.hpp"
#include <pluginlib/class_loader.hpp>
#include <rclcpp/rclcpp.hpp>
#include <chrono>
#include <cmath>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <stdexcept>
#include <thread>

namespace p = mr2_devices_robstride::protocol;
using Steady = std::chrono::steady_clock;

static void require(bool condition, const std::string &message) {
  if (!condition) throw std::runtime_error(message);
}

class Monitor {
public:
  Monitor(const std::string &iface, int motor, const std::string &log)
      : motor_(motor), frames_(log) {
    require(bool(frames_), "Cannot create CAN frame log");
    fd_ = ::socket(PF_CAN, SOCK_RAW | SOCK_NONBLOCK, CAN_RAW);
    require(fd_ >= 0, "Cannot create monitor socket");
    can_filter filters[] = {
        {CAN_EFF_FLAG | uint32_t(motor << 8) | 253U,
         CAN_EFF_FLAG | CAN_RTR_FLAG | 0xffffU},
        {CAN_EFF_FLAG | 0xfd00U | uint32_t(motor),
         CAN_EFF_FLAG | CAN_RTR_FLAG | 0x00ffffffU}};
    // Kernel CAN_RAW_FILTER masks differ from our user-space reply mask:
    // CAN_ERR_FLAG in a kernel mask subscribes to the error receive list.
    // Data sockets exclude errors by default; use CAN_RAW_ERR_FILTER for them.
    require(setsockopt(fd_, SOL_CAN_RAW, CAN_RAW_FILTER, filters, sizeof(filters)) == 0,
            "Cannot set monitor filters");
    sockaddr_can address{};
    address.can_family = AF_CAN;
    address.can_ifindex = if_nametoindex(iface.c_str());
    require(address.can_ifindex != 0 &&
                bind(fd_, reinterpret_cast<sockaddr *>(&address), sizeof(address)) == 0,
            "Cannot bind monitor to " + iface);
  }
  ~Monitor() { if (fd_ >= 0) ::close(fd_); }
  void drain() {
    can_frame frame{};
    for (int i = 0; i < 512 && ::read(fd_, &frame, sizeof(frame)) == sizeof(frame); ++i) {
      frames_ << Steady::now().time_since_epoch().count() << ' ' << std::hex
              << std::setw(8) << std::setfill('0') << frame.can_id << '#';
      for (int j = 0; j < frame.can_dlc && j < 8; ++j)
        frames_ << std::setw(2) << unsigned(frame.data[j]);
      frames_ << std::dec << '\n';
      if (p::is_reply(frame, p::kFeedback, 253, motor_)) {
        last = p::decode(frame, {});
        have_status = true;
        fault |= last.faults != 0 || last.temperature > 60.0;
        if (last.mode == 0) {
          ++stopped_samples;
          stopped_velocity_sum += last.velocity;
        }
      } else if (p::is_reply(frame, p::kFault, 253, motor_)) {
        fault |= p::u32_le(frame.data) != 0 || p::u32_le(frame.data + 4) != 0;
      }
    }
    frames_.flush();
  }
  p::Feedback last{};
  bool have_status{false}, fault{false};
  unsigned stopped_samples{0};
  double stopped_velocity_sum{0};
private:
  int fd_{-1}, motor_;
  std::ofstream frames_;
};

int main(int argc, char **argv) {
  if (argc != 5 || std::string(argv[1]) != "--run") {
    std::cerr << "Usage: robstride_smoke_test --run CAN_IFACE MOTOR_ID OUTPUT.csv\n"
              << "Moves only this motor at +0.2/-0.2 rad/s for 1 s each, 1 A limit.\n";
    return 2;
  }
  const std::string iface = argv[2], path = argv[4];
  int motor;
  try {
    size_t used;
    const std::string id = argv[3];
    motor = std::stoi(id, &used);
    require(used == id.size() && motor >= 1 && motor <= 127, "Motor ID must be 1..127");
  } catch (const std::exception &error) {
    std::cerr << error.what() << '\n';
    return 2;
  }
  rclcpp::init(0, nullptr);
  auto node = rclcpp::Node::make_shared("rs06_single_motor_test");
  pluginlib::ClassLoader<CanDevice> loader("mr2_can_bus_core", "CanDevice");
  std::shared_ptr<CanDevice> device;
  bool configured = false, passed = false, stopped = false;
  try {
    Monitor monitor(iface, motor, path + ".frames");
    std::ofstream csv(path);
    require(bool(csv), "Cannot create state log");
    csv << "elapsed_s,phase,command_rad_s,position_rad,velocity_rad_s,torque_nm,mode,temperature_c\n";
    device = loader.createSharedInstance("mr2_devices_robstride/RobstrideDevice");
    hardware_interface::ComponentInfo info;
    info.name = "rs06_single_motor_test";
    hardware_interface::InterfaceInfo command;
    command.name = "velocity";
    info.command_interfaces.push_back(command);
    info.parameters = {{"can_iface", iface}, {"motor_id", std::to_string(motor)},
                       {"host_id", "253"}, {"current_limit_a", "1.0"},
                       {"max_velocity_rad_s", "0.2"}, {"acceleration_rad_s2", "0.5"},
                       {"can_timeout_ms", "200"}, {"feedback_timeout_ms", "200"},
                       {"activation_timeout_ms", "500"}};
    device->configure(info, node.get());
    configured = true;
    const bool activated = device->on_activate();
    monitor.drain();
    require(activated, "Plugin activation failed; see raw frame log");
    require(monitor.have_status && !monitor.fault && monitor.last.mode == 2,
            "Independent monitor did not confirm fault-free running status");
    double *target = nullptr;
    device->export_command(target);
    require(target != nullptr, "No velocity command memory");
    CanFeedback feedback;
    const auto start = Steady::now();
    double reference = 0;
    bool reference_set = false;
    auto phase = [&](const std::string &name, double velocity, double seconds) {
      const auto begin = Steady::now();
      auto next = begin;
      double sum = 0;
      int count = 0;
      while (std::chrono::duration<double>(Steady::now() - begin).count() < seconds) {
        require(rclcpp::ok(), "Test interrupted");
        *target = velocity;
        device->process(node->now());
        next += std::chrono::milliseconds(10);
        std::this_thread::sleep_until(next);
        monitor.drain();
        device->copy_feedback(feedback);
        require(!monitor.fault, "Motor fault or temperature >60 C");
        require(std::isfinite(feedback.position) && std::isfinite(feedback.velocity),
                "Plugin rejected feedback (fault, reset, or timeout)");
        require(std::abs(feedback.velocity) < 0.8, "Unexpected speed; stopping");
        if (reference_set)
          require(std::abs(feedback.position - reference) < 0.5,
                  "Test travel exceeds 0.5 rad; stopping");
        csv << std::setprecision(12)
            << std::chrono::duration<double>(Steady::now() - start).count() << ','
            << name << ',' << velocity << ',' << feedback.position << ','
            << feedback.velocity << ',' << feedback.effort << ','
            << unsigned(monitor.last.mode) << ',' << monitor.last.temperature << '\n';
        if (std::chrono::duration<double>(Steady::now() - begin).count() > seconds - 0.25) {
          sum += feedback.velocity;
          ++count;
        }
      }
      csv.flush();
      require(count > 0, "No samples in test phase");
      std::cout << name << " mean_velocity=" << sum / count
                << " rad/s position=" << feedback.position << " rad\n";
      return sum / count;
    };
    // Leave time for the configured acceleration ramp and the firmware's
    // noisy low-speed estimate to settle before evaluating a zero target.
    phase("initial_zero", 0, 1.0);
    reference = feedback.position;
    reference_set = true;
    const auto forward = phase("forward", 0.2, 1.0);
    const double forward_delta = feedback.position - reference;
    phase("zero_between", 0, 1.0);
    const auto reverse_reference = feedback.position;
    const auto reverse = phase("reverse", -0.2, 1.0);
    const double reverse_delta = feedback.position - reverse_reference;
    const auto zero = phase("final_zero", 0, 1.0);
    monitor.stopped_samples = 0;
    monitor.stopped_velocity_sum = 0;
    device->on_deactivate();
    const auto deadline = Steady::now() + std::chrono::seconds(1);
    auto retry = Steady::now() + std::chrono::milliseconds(100);
    do {
      std::this_thread::sleep_for(std::chrono::milliseconds(10));
      monitor.drain();
      // RS-06's instantaneous feedback has measurable zero-speed noise. Use
      // several fresh disabled-status samples, alongside the settled zero
      // phase above, instead of treating one noisy sample as stop failure.
      stopped = monitor.have_status && monitor.last.mode == 0 &&
                monitor.stopped_samples >= 3 &&
                std::abs(monitor.stopped_velocity_sum / monitor.stopped_samples) < 0.05;
      if (!stopped && Steady::now() >= retry) {
        device->on_deactivate();
        retry = Steady::now() + std::chrono::milliseconds(100);
      }
    } while (!stopped && Steady::now() < deadline);
    require(stopped, "Stop/reset status was not confirmed");
    require(!monitor.fault, "Motor fault during stop");
    require(std::abs(forward - 0.2) < 0.08 && forward_delta > 0.03,
            "Forward motion did not follow the target");
    require(std::abs(reverse + 0.2) < 0.08 && reverse_delta < -0.03,
            "Reverse motion did not follow the target");
    require(std::abs(zero) < 0.05, "Zero-speed target did not settle");
    std::cout << "PASS motor=" << motor << " forward_delta=" << forward_delta
              << " reverse_delta=" << reverse_delta << " stop_confirmed=true"
              << " stop_samples=" << monitor.stopped_samples
              << " stop_mean_velocity=" << monitor.stopped_velocity_sum / monitor.stopped_samples
              << '\n';
    passed = true;
  } catch (const std::exception &error) {
    std::cerr << "FAIL: " << error.what() << '\n';
  }
  if (configured) {
    // Repeat terminal commands before stopping the shared transport, including
    // exception paths. Never leave a nonzero target behind after this process.
    for (int i = 0; i < 3; ++i) {
      device->on_deactivate();
      std::this_thread::sleep_for(std::chrono::milliseconds(20));
    }
    device->stop_transport();
  }
  CanBusRegistry::stop_all();
  rclcpp::shutdown();
  return passed && stopped ? 0 : 1;
}
