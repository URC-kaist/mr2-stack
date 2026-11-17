#pragma once
#include "mr2_control/types.hpp"
#include <algorithm>
#include <array>
#include <cmath>
namespace mr2_control {
struct DriveConfig {
  double wheel_base{0.95386}, track_width{0.6504}, wheel_radius{0.17};
  double max_wheel_linear_speed{0.4}, max_steer{2.35619}, timeout{0.5};
  double rate_limit_vx{1.5}, rate_limit_vy{1.5}, rate_limit_wz{1.0};
  double steering_error_ratio_rad{40.0 * M_PI / 180.0};
  std::array<bool, 4> odom_wheel_drive_enabled{true, true, true, true};
  std::array<bool, 4> odom_wheel_steer_enabled{true, true, true, true};
};
class DriveProcessor {
public:
  explicit DriveProcessor(DriveConfig config = {}) : config_(config) {}
  const std::array<double, 8> &update(const DriveCommand &command,
                                      const std::array<double, 8> &state,
                                      Clock::time_point now, double dt) {
    const bool timed_out =
        !command.valid ||
        std::chrono::duration<double>(now - command.received).count() >
            config_.timeout;
    dt = std::max(0.0, dt);
    const double target_vx = timed_out ? 0.0 : command.twist.linear.x;
    const double target_vy = timed_out ? 0.0 : command.twist.linear.y;
    const double target_wz = timed_out ? 0.0 : command.twist.angular.z;

    limited_twist_.linear.x = applyRateLimit(target_vx, limited_twist_.linear.x,
                                             config_.rate_limit_vx, dt);
    limited_twist_.linear.y = applyRateLimit(target_vy, limited_twist_.linear.y,
                                             config_.rate_limit_vy, dt);
    limited_twist_.angular.z = applyRateLimit(
        target_wz, limited_twist_.angular.z, config_.rate_limit_wz, dt);

    const double vx = limited_twist_.linear.x;
    const double vy = limited_twist_.linear.y;
    const double wz = limited_twist_.angular.z;

    if (config_.wheel_radius <= 0.0) {

      targets_.fill(0.0);
    } else if (state.size() < 8) {

      targets_.fill(0.0);
    } else {
      std::array<double, 4> current_steer{};
      bool steer_feedback_valid = true;
      for (size_t i = 0; i < 4; ++i) {
        const double val = state[i + 4];
        if (std::isfinite(val)) {
          current_steer[i] = val;
        } else {
          steer_feedback_valid = false;
        }
      }

      if (!steer_feedback_valid) {
        targets_.fill(0.0);
      } else {
        const auto wheels = wheelPositions();
        std::array<double, 4> speed{};
        std::array<double, 4> steer{};

        auto ang_distance = [](double a, double b) {
          // Smallest absolute difference between two angles (wrap at 2*pi)
          const double diff = std::remainder(a - b, 2.0 * M_PI);
          return std::abs(diff);
        };

        for (size_t i = 0; i < wheels.size(); ++i) {
          const auto &w = wheels[i];
          const double vx_i = vx - wz * w[1];
          const double vy_i = vy + wz * w[0];
          const double v_lin = std::hypot(vx_i, vy_i);

          constexpr double kVelStop = 1e-4;
          if (v_lin < kVelStop) { // Legacy leaves the initialized steering target at zero.
            speed[i] = 0.0;
            continue;
          }

          const double v_lin_limited =
              (config_.max_wheel_linear_speed > 0.0)
                  ? std::min(v_lin, config_.max_wheel_linear_speed)
                  : v_lin;

          double ang = std::atan2(vy_i, vx_i);
          double w_ang = v_lin_limited / config_.wheel_radius;

          // Two equivalent steering solutions: (ang, w_ang) or (ang +/- pi,
          // -w_ang).
          double ang_alt = ang;
          double w_ang_alt = w_ang;
          if (ang > 0) {
            ang_alt = ang - M_PI;
          } else {
            ang_alt = ang + M_PI;
          }
          w_ang_alt *= -1.0;

          const bool reachable1 = std::abs(ang) <= config_.max_steer;
          const bool reachable2 = std::abs(ang_alt) <= config_.max_steer;

          // Choose the solution closest to current steering angle for that
          // wheel.
          if (reachable1 && !reachable2) {
            ang = std::clamp(ang, -config_.max_steer, config_.max_steer);
          } else if (!reachable1 && reachable2) {
            ang = std::clamp(ang_alt, -config_.max_steer, config_.max_steer);
            w_ang = w_ang_alt;
          } else if (!reachable1 && !reachable2) {
            const double unclamped = ang;
            ang = std::clamp(unclamped, -config_.max_steer, config_.max_steer);
            w_ang = 0.0; // cannot realize kinematics at this angle

          } else { // both reachable
            const double cand1 =
                std::clamp(ang, -config_.max_steer, config_.max_steer);
            const double cand2 =
                std::clamp(ang_alt, -config_.max_steer, config_.max_steer);
            if (ang_distance(cand2, current_steer[i]) <
                ang_distance(cand1, current_steer[i])) {
              ang = cand2;
              w_ang = w_ang_alt;
            } else {
              ang = cand1;
            }
          }

          steer[i] = std::clamp(ang, -config_.max_steer, config_.max_steer);
          speed[i] = w_ang;
        }

        // Apply a global speed scale based on the worst steering error.
        double max_steer_err = 0.0;
        for (size_t i = 0; i < wheels.size(); ++i) {
          const double steer_err = ang_distance(steer[i], current_steer[i]);
          if (steer_err > max_steer_err) {
            max_steer_err = steer_err;
          }
        }
        if (config_.max_steer > 0.0) {
          const double denom = config_.steering_error_ratio_rad > 0.0
                                   ? config_.steering_error_ratio_rad
                                   : config_.max_steer;
          const double norm = denom > 0.0 ? (max_steer_err / denom) : 0.0;
          constexpr double power = 2.0; // square the normalized error
          double scale = 1.0 - std::pow(norm, power);
          scale = std::clamp(scale, 0.0, 1.0);
          for (double &v : speed) {
            v *= scale;
          }
        }

        for (size_t i = 0; i < 4; ++i) {
          targets_[i] = speed[i];
          targets_[i + 4] = steer[i];
        }
      }
    }

    // Legacy update has an unconditional final zero on timeout (including
    // steer).
    if (timed_out)
      targets_.fill(0.0);
    return targets_;
  }
  Twist odometry(std::array<double, 4> wheel_ang_vel,
                 std::array<double, 4> steer_angle) const {
    for (auto &v : wheel_ang_vel)
      if (!std::isfinite(v))
        v = 0.0;
    for (auto &v : steer_angle)
      if (!std::isfinite(v))
        v = 0.0;
    const auto wheel_pos = wheelPositions();

    // Average-based estimation: project each wheel's measured speed into linear
    // body components, then estimate yaw from residual per-wheel lever arm.
    double vx_sum = 0.0;
    double vy_sum = 0.0;
    size_t lin_count = 0;

    for (size_t i = 0; i < 4; ++i) {
      if (!config_.odom_wheel_drive_enabled[i] ||
          !config_.odom_wheel_steer_enabled[i]) {
        continue;
      }
      const double theta = steer_angle[i];
      const double c = std::cos(theta);
      const double s = std::sin(theta);
      const double v = wheel_ang_vel[i] * config_.wheel_radius;
      vx_sum += v * c;
      vy_sum += v * s;
      ++lin_count;
    }

    const double vx = lin_count ? vx_sum / static_cast<double>(lin_count) : 0.0;
    const double vy = lin_count ? vy_sum / static_cast<double>(lin_count) : 0.0;

    double wz_sum = 0.0;
    size_t wz_count = 0;
    for (size_t i = 0; i < 4; ++i) {
      if (!config_.odom_wheel_drive_enabled[i] ||
          !config_.odom_wheel_steer_enabled[i]) {
        continue;
      }
      const double theta = steer_angle[i];
      const double c = std::cos(theta);
      const double s = std::sin(theta);
      const double v = wheel_ang_vel[i] * config_.wheel_radius;
      const double lever = -c * wheel_pos[i][1] + s * wheel_pos[i][0];
      if (std::abs(lever) < 1e-4) {
        continue; // avoid divide-by-near-zero when lever arm vanishes
      }
      const double wz_i = (v - c * vx - s * vy) / lever;
      wz_sum += wz_i;
      ++wz_count;
    }
    const double wz = wz_count ? wz_sum / static_cast<double>(wz_count) : 0.0;

    Twist result;
    result.linear.x = vx;
    result.linear.y = vy;
    result.angular.z = wz;
    return result;
  }

private:
  std::array<std::array<double, 2>, 4> wheelPositions() const {
    const double hx = config_.wheel_base * .5, hy = config_.track_width * .5;
    return {{{hx, hy}, {hx, -hy}, {-hx, hy}, {-hx, -hy}}};
  }
  static double applyRateLimit(double target, double prev, double rate,
                               double dt) {
    if (rate <= 0.0 || dt <= 0.0)
      return target;
    return prev + std::clamp(target - prev, -rate * dt, rate * dt);
  }
  DriveConfig config_;
  Twist limited_twist_;
  std::array<double, 8> targets_{};
};
} // namespace mr2_control
