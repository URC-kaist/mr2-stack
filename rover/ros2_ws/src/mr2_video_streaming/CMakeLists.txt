cmake_minimum_required(VERSION 3.8)
project(mr2_video_streaming)

if(CMAKE_COMPILER_IS_GNUCXX OR CMAKE_CXX_COMPILER_ID MATCHES "Clang")
  add_compile_options(-Wall -Wextra -Wpedantic)
endif()

find_package(ament_cmake REQUIRED)
find_package(mr2_latency_msgs REQUIRED)
find_package(nlohmann_json REQUIRED)
find_package(PkgConfig REQUIRED)
find_package(rclcpp REQUIRED)
find_package(sensor_msgs REQUIRED)
find_package(std_srvs REQUIRED)
find_package(std_msgs REQUIRED)

pkg_check_modules(GSTREAMER REQUIRED IMPORTED_TARGET
  gstreamer-1.0
  gstreamer-app-1.0
  gstreamer-video-1.0
  gstreamer-rtp-1.0
)

add_executable(video_streaming_node
  src/video_streaming_node.cpp
)
target_compile_features(video_streaming_node PUBLIC cxx_std_17)
target_link_libraries(video_streaming_node
  PkgConfig::GSTREAMER
  nlohmann_json::nlohmann_json
)
ament_target_dependencies(video_streaming_node
  mr2_latency_msgs
  rclcpp
  sensor_msgs
  std_srvs
  std_msgs
)

add_executable(video_receiver src/video_receiver.cpp)
target_compile_features(video_receiver PUBLIC cxx_std_17)
target_link_libraries(video_receiver PkgConfig::GSTREAMER)

add_executable(video_sender src/video_sender.cpp)
target_compile_features(video_sender PUBLIC cxx_std_17)
target_link_libraries(video_sender PkgConfig::GSTREAMER nlohmann_json::nlohmann_json)

install(TARGETS video_streaming_node video_sender video_receiver
  DESTINATION lib/${PROJECT_NAME}
)

if(BUILD_TESTING)
  enable_testing()
  add_executable(test_video_control test/test_video_control.cpp)
  target_compile_features(test_video_control PUBLIC cxx_std_17)
  target_include_directories(test_video_control PRIVATE src)
  add_test(NAME video_control COMMAND test_video_control)
  add_executable(test_video_pacer test/test_video_pacer.cpp)
  target_compile_features(test_video_pacer PUBLIC cxx_std_17)
  target_include_directories(test_video_pacer PRIVATE src)
  target_link_libraries(test_video_pacer PkgConfig::GSTREAMER)
  add_test(NAME video_pacer COMMAND test_video_pacer)
  add_executable(test_receiver_correlation test/test_receiver_correlation.cpp)
  target_compile_features(test_receiver_correlation PUBLIC cxx_std_17)
  target_include_directories(test_receiver_correlation PRIVATE src)
  target_link_libraries(test_receiver_correlation PkgConfig::GSTREAMER)
  add_test(NAME receiver_correlation COMMAND test_receiver_correlation)
  add_executable(test_receive_freshness test/test_receive_freshness.cpp)
  target_compile_features(test_receive_freshness PUBLIC cxx_std_17)
  target_include_directories(test_receive_freshness PRIVATE src)
  target_link_libraries(test_receive_freshness PkgConfig::GSTREAMER)
  add_test(NAME receive_freshness COMMAND test_receive_freshness)
  add_executable(test_link_probe test/test_link_probe.cpp)
  target_compile_features(test_link_probe PUBLIC cxx_std_17)
  target_include_directories(test_link_probe PRIVATE src)
  target_link_libraries(test_link_probe PkgConfig::GSTREAMER)
  add_test(NAME link_probe COMMAND test_link_probe)
  find_package(ament_lint_auto REQUIRED)
  set(ament_cmake_copyright_FOUND TRUE)
  set(ament_cmake_cpplint_FOUND TRUE)
  ament_lint_auto_find_test_dependencies()
endif()

ament_package()
