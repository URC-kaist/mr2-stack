# URC 2027 마이그레이션 준비: Orin NX 의존성 버전 기준표

## 목적과 조사 범위

Ubuntu 24.04 전환 전에 현재 NX의 의존성을 재현하고 비교할 수 있도록 작성한 기준표다.
MR2 Docs의 [Global Update · URC 2027](https://urc-kaist.github.io/docs/docs/global-update-project/ko/),
[업데이트 범위와 원칙](https://urc-kaist.github.io/docs/docs/global-update-project/ko/platform/),
[분야별 작업과 협업](https://urc-kaist.github.io/docs/docs/global-update-project/ko/work-packages/),
[기술 조사 결과](https://urc-kaist.github.io/docs/docs/global-update-project/ko/research/)를 읽고 실제 NX에서 수집했다.
문서가 요구하는 현재 NX 환경 기록 중 **의존성 버전 조사**에 해당한다.

**표의 버전은 현재 설치 상태이며 Ubuntu 24.04용 확정 버전이 아니다.** 프로젝트의 목표 조합은
Ubuntu 24.04 / ROS 2 Jazzy / Gazebo Harmonic이다. 새 BSP와 Python·ROS·네이티브 ABI에 맞는
목표 버전 선정과 동작 검증은 이 기준표를 바탕으로 진행한다.

- 소속은 실행 컴퓨터가 아니라 저장소의 사용 영역이다. `base`는 `base/gateway`, `rover`는 ROS·장치·영상·인지,
  `dashboard`는 웹 UI, `scripts`는 설치·배포·계측 도구를 뜻한다. 여러 영역이 쓰면 함께 적었다.
- 직접 선언된 npm/ROS 의존성과 CMake·Python import·launch·스크립트에서 확인한 외부 의존성을 정리했다.
  ROS 메시지·빌드 도구도 포함했다. 전체 전이 패키지 버전은 함께 저장한 원자료에서 확인할 수 있다.
- `미설치/미발견`은 이번에 사용한 Python·ROS 경로, dpkg, PATH에서 확인되지 않았다는 뜻이다.
  설치되어 있다는 사실과 기능이 활성화되거나 정상 작동한다는 사실은 구분한다.
- 버전 수집과 일부 모듈 import만 수행했다. 패키지 설치·업데이트, ROS 노드 실행, CAN 송신,
  카메라 스트리밍, 서비스·네트워크 변경은 수행하지 않았다.

## 1. 조사한 장비·소스·실행 환경

| 항목 | 확인값 | 소속 / 의미 |
| --- | --- | --- |
| 조사 시각 | 2026-11-17T03:51:26.521233+00:00 | UTC 기준 원자료 생성 시각; 조사일 2026-11-17 KST |
| 장비 모델 | NVIDIA Jetson Orin NX Engineering Reference Developer Kit | rover; device-tree 보고값. 실물 캐리어 보드의 정확한 제품명은 별도 확인 필요 |
| CPU 아키텍처 | aarch64 / Debian arm64 | base, rover, dashboard, scripts |
| Ubuntu | 22.04.5 LTS (Jammy) | base, rover, dashboard, scripts; 현재 호스트 |
| L4T / BSP | 36.5.0-20260115194252 | rover; /etc/nv_tegra_release의 R36, REVISION 5.0과 대조 |
| Linux 커널 | 5.15.185-tegra | rover, scripts; SocketCAN·USB·V4L2·장치 드라이버 기반 |
| JetPack 메타패키지 | nvidia-jetpack 미설치 | rover; L4T/CUDA 버전은 확인했으나 JetPack 제품 버전은 임의 환산하지 않음 |
| 저장소 브랜치 | 2026-nepo-demo | 이 NX의 로컬 작업 트리 |
| 저장소 HEAD | `e57a3e5888f72b0e1efec74fcef2c45a12f9ab4b` | 수정·미추적 파일이 있는 상태. HEAD만으로 현재 소스·기존 바이너리를 재현할 수 없음 |
| Node.js / npm / Node ABI | v24.15.0 / 11.12.1 / 137 | base, dashboard, scripts; .nvmrc=24.15.0, engines >=24 <25, packageManager npm@11.12.1 |
| Python | 3.10.12 (/usr/bin/python3 → python3.10) | rover, scripts; python3 메타패키지 버전과 인터프리터 버전은 다름 |
| 선택된 rover profile | flat; {"profile": "flat", "common": {"enable_flat_motor": "false", "enable_gnss": "false"}} | 다음 launch용 저장 설정. 현재 실행 중인 전체 기능 목록을 뜻하지 않음 |

ROS 환경은 `scripts/run_rover_direct.bash`와 동일하게 `/opt/ros/humble/setup.bash` 다음에
`rover/ros2_ws/install/setup.bash`를 source하여 조사했다. 후자의 prefix chain에는 저장소 밖의
`/home/mr2/ros2_ws/install`이 포함된다. RealSense는 이 외부 워크스페이스에서 제공된다.
기본 브랜치를 조사한 프로젝트 문서의 SHA와 이번 NX의 SHA·수정 상태는 서로 다르다.

설치된 ROS 패키지의 `share/<package>/package.xml`과 현재 소스의 `package.xml`을 각각 수집했다.
버전 문자열이 같아도 기존 빌드 결과와 수정된 소스 내용이 같다는 보장은 없다.

## 2. 공통 네이티브 빌드·런타임 의존성

| 의존성 이름 | NX 설치 버전 (Debian 전체 버전) | 소속 | 제공 기능 |
| --- | --- | --- | --- |
| build-essential | 12.9ubuntu3 | rover, base, scripts | C/C++ 빌드 기본 도구; rclnodejs·serialport의 네이티브 빌드에도 사용 |
| gcc | 4:11.2.0-1ubuntu1 | rover, base | C 컴파일러 선택 메타패키지 |
| g++ | 4:11.2.0-1ubuntu1 | rover, base | C++ 컴파일러 선택 메타패키지; 실제 g++는 11.4.0 |
| cmake | 3.22.1-1ubuntu1.22.04.2 | rover, base | ROS C++ 및 네이티브 애드온 빌드 구성 |
| make | 4.3-4.1build1 | rover, base | 네이티브 빌드 실행 |
| pkg-config | 0.29.2-1ubuntu3 | rover, base | GStreamer·libusb·libcurl 등 헤더/라이브러리 탐색 |
| libc6 | 2.35-0ubuntu3.13 | base, rover, scripts | glibc 런타임과 네이티브 바이너리 ABI |
| libstdc++6 | 12.3.0-1ubuntu1~22.04.3 | base, rover | C++ 표준 라이브러리 런타임 |
| python3-dev | 3.10.6-1~22.04.1 | rover, base | Python 확장·ROS 인터페이스 빌드 헤더 |
| libeigen3-dev | 3.4.0-2ubuntu2 | rover | 좌표 변환·선형대수; Eigen3 CMake 의존성 |
| libyaml-cpp-dev | 0.7.0+dfsg-8build1 | rover | C++ 제어·설정 YAML 파싱 |
| libyaml-dev | 0.2.2-1build2 | rover | C YAML 라이브러리; CMake의 yaml 탐색에 대응 |
| nlohmann-json3-dev | 3.10.5-2 | rover | 영상 스트림 JSON 설정; rosdep 키 nlohmann-json-dev에 대응 |
| libgeographic-dev | 1.52-1 | rover | GeographicLib; 위도·경도/지리 좌표 연산 |
| libpcl-dev | 1.12.1+dfsg-3build1 | rover | PCL 점군 처리; CMake find_package(PCL) 의존성 |
| libcurl4-openssl-dev | 7.81.0-1ubuntu1.23 | rover | NTRIP 클라이언트 HTTP 전송; libcurl-dev/CURL에 대응 |
| libusb-1.0-0-dev | 2:1.0.25-1ubuntu2 | rover | u-blox·카메라 USB 통신; libusb-1.0-dev에 대응 |
| libopencv-dev | 4.5.4+dfsg-9ubuntu4 | rover | C++ panorama·비전·영상 처리; Python cv2와 별도 버전 |
| python3-colcon-common-extensions | 0.3.0-100 | rover, scripts | colcon ROS 워크스페이스 빌드 확장 모음 |
| python3-colcon-core | 0.20.1+upstream-1 | rover, scripts | colcon 빌드·테스트 실행기 |
| python3-rosdep | 0.26.0-1 | rover, scripts | package.xml 의존성을 OS 패키지로 해석·설치 |
| python3-empy | 3.3.4-2 | rover | ROS 메시지 코드 생성 템플릿 |
| python3-setuptools | 59.6.0-1.2ubuntu0.22.04.3 | rover | ament_python/setup.py 기반 패키지 설치 |

버전 근거: [dpkg 설치 목록](orin-nx-2026-11-17/dpkg-installed.tsv),
[네이티브 조회 결과](orin-nx-2026-11-17/native-details.json).
`g++ --version`은 `11.4.0`을 보고하므로, gcc/g++ 메타패키지의 `11.2.0` 문자열을 실제 컴파일러 버전으로 사용하면 안 된다.
PCL은 dpkg 설치 상태를 기록한 것이며 이번 `pkg-config pcl_common` 조회는 실패했다.
클린 빌드에서 CMake 탐색 성공 여부를 별도로 확인해야 한다.

## 3. rover: ROS 2 외부 의존성

아래 버전은 활성 ROS prefix에서 읽은 패키지 릴리스 버전이다. Debian 빌드 접미사까지 필요한 경우
`ros-humble-<이름의 _를 -로 바꾼 값>`을 [dpkg 목록](orin-nx-2026-11-17/dpkg-installed.tsv)에서 찾는다.
RealSense처럼 소스 설치한 패키지는 dpkg 대신 외부 저장소 SHA를 함께 사용한다.
각 패키지의 실제 prefix와 의존하는 MR2 패키지는 [ROS 원자료](orin-nx-2026-11-17/ros-packages.json)에 있다.

| 의존성 이름 | NX 설치 버전 (ROS package.xml) | 소속 | 제공 기능 |
| --- | --- | --- | --- |
| rclcpp | 16.0.19 | rover | C++ ROS 노드·publisher/subscriber·executor |
| rclcpp_action | 16.0.19 | rover | C++ action 클라이언트·서버 |
| rclcpp_components | 16.0.19 | rover | ROS 노드 component 동적 로딩 |
| rclcpp_lifecycle | 16.0.19 | rover | 노드 lifecycle 상태 전환 |
| rclpy | 3.3.21 | rover, scripts | Python ROS 노드·통신; scripts의 실험 기록에도 사용 |
| rmw_fastrtps_cpp | 6.2.10 | rover | 설치된 Fast DDS 기반 ROS 미들웨어 구현; 운용 프로세스의 실제 선택은 별도 확인 |
| rmw_implementation | 2.8.5 | rover | ROS 미들웨어 구현 동적 선택 |
| launch | 1.0.14 | rover | 프로세스 실행·launch 인자·조건 처리 |
| launch_ros | 0.19.13 | rover | ROS 노드·파라미터·namespace launch 통합 |
| ament_index_cpp | 1.4.1 | rover | C++에서 패키지 share 경로·리소스 탐색 |
| ament_index_python | 1.4.1 | rover | Python launch에서 패키지·리소스 경로 탐색 |
| pluginlib | 5.1.3 | rover | 제어·Nav2·grid map 플러그인 로딩 |
| ros2_control | 2.53.1 | rover | 하드웨어·컨트롤러 프레임워크 메타패키지 |
| hardware_interface | 2.53.1 | rover | CAN 장치 state/command 인터페이스 |
| transmission_interface | 2.53.1 | rover | actuator와 joint 사이 전달 관계 |
| controller_interface | 2.53.1 | rover | 자체 구동·조향 컨트롤러 기반 API |
| controller_manager | 2.53.1 | rover | 하드웨어/컨트롤러 로딩·spawner·lifecycle 관리 |
| controller_manager_msgs | 2.53.1 | rover | 컨트롤러 조회·전환 서비스 |
| ros2_controllers | 2.52.1 | rover | 표준 컨트롤러 메타패키지 |
| joint_state_broadcaster | 2.52.1 | rover | joint state 상태 토픽 발행 |
| position_controllers | 2.52.1 | rover | 관절 위치 명령 전달 |
| moveit_servo | 2.5.9 | rover | 매니퓰레이터 Cartesian/joint 실시간 Servo |
| moveit_kinematics | 2.5.9 | rover | 역기구학 플러그인 |
| moveit_planners | 2.5.9 | rover | MoveIt 경로 계획기 모음 |
| moveit_simple_controller_manager | 2.5.9 | rover | MoveIt과 trajectory controller 연결 |
| moveit_configs_utils | 2.5.9 | rover | MoveIt launch 설정 생성·로딩 |
| moveit_ros_visualization | 2.5.9 | rover | MoveIt RViz 시각화 |
| joy | 3.3.0 | rover | 조이스틱 입력을 ROS Joy 메시지로 발행 |
| robot_state_publisher | 3.0.3 | rover | URDF·joint state에서 TF 발행 |
| xacro | 2.1.1 | rover | URDF·ros2_control 매크로 확장 |
| tf2 | 0.25.20 | rover | 시간에 따른 좌표계 변환 |
| tf2_ros | 0.25.20 | rover | ROS TF buffer/listener/broadcaster |
| tf2_geometry_msgs | 0.25.20 | rover | geometry 메시지의 좌표계 변환 |
| tf2_eigen | 0.25.20 | rover | TF와 Eigen 자료형 변환 |
| nav2_bringup | 1.1.20 | rover | 자율 주행 Nav2 launch·설정 |
| nav2_common | 1.1.20 | rover | Nav2 launch의 YAML 재작성 등 공통 도구 |
| nav2_lifecycle_manager | 1.1.20 | rover | Nav2 서버 lifecycle 통합 관리 |
| nav2_map_server | 1.1.20 | rover | 지도 로딩·발행 |
| nav2_costmap_2d | 1.1.20 | rover | 장애물·주행 가능성 costmap과 자체 layer 기반 |
| nav2_util | 1.1.20 | rover | Nav2 공통 C++ 유틸리티 |
| nav2_behavior_tree | 1.1.20 | rover | Nav2 behavior tree 통합 |
| nav2_bt_navigator | 1.1.20 | rover | BT 기반 navigation action 실행 |
| nav2_planner | 1.1.20 | rover | 경로 계획 서버 |
| nav2_controller | 1.1.20 | rover | 경로 추종 제어 서버 |
| nav2_smoother | 1.1.20 | rover | 계획 경로 smoothing 서버 |
| nav2_behaviors | 1.1.20 | rover | 복구·대기·회전 등 navigation behavior 서버 |
| nav2_waypoint_follower | 1.1.20 | rover | 경유점 순회 |
| nav2_velocity_smoother | 1.1.20 | rover | 속도 명령 smoothing |
| nav2_msgs | 1.1.20 | rover | Nav2 action·메시지·서비스 |
| behaviortree_cpp_v3 | 3.8.7 | rover | 자율 주행 behavior tree 실행 엔진 |
| robot_localization | 3.5.4 | rover | GNSS·IMU·odometry 융합, navsat 좌표 변환 |
| imu_filter_madgwick | 2.1.5 | rover | IMU 자세 추정 필터 |
| grid_map_core | 2.0.1 | rover | 다중 레이어 지형 격자 자료구조 |
| grid_map_ros | 2.0.1 | rover | ROS와 grid map 간 변환 |
| grid_map_msgs | 2.0.1 | rover | 지형 grid map 메시지 |
| grid_map_filters | 2.0.1 | rover | 지형 grid map 필터 파이프라인 |
| filters | 2.2.2 | rover | 필터 체인·플러그인 프레임워크 |
| pcl_conversions | 2.4.5 | rover | ROS PointCloud2와 PCL 자료형 변환 |
| aruco_opencv | 2.4.1 | rover | ArUco 마커 검출·추적 |
| aruco_opencv_msgs | 2.4.1 | rover | ArUco 검출 결과 메시지 |
| camera_calibration_parsers | 3.1.12 | rover | 카메라 내부 파라미터 읽기·쓰기 |
| image_geometry | 3.2.1 | rover | 카메라 모델·픽셀/3D 투영 |
| cv_bridge | 3.2.1 | rover | ROS 이미지와 OpenCV 변환; 외부 카메라 스택의 전이 의존성 |
| message_filters | 4.3.15 | rover | RGB·depth·camera_info 등 메시지 시간 동기화 |
| realsense2_camera | 4.57.7 | rover | RealSense RGB-D 카메라 ROS 드라이버; 외부 ~/ros2_ws 설치 |
| realsense2_camera_msgs | 4.57.7 | rover | RealSense 전용 메시지; 외부 ~/ros2_ws 설치 |
| realsense2_description | 4.57.7 | rover | RealSense URDF·장치 모델; 외부 ~/ros2_ws 설치 |
| v4l2_camera | 0.6.2 | rover | USB/UVC 카메라 ROS 드라이버 |
| rosbridge_server | 2.0.5 | rover, base, dashboard | 브라우저/gateway의 WebSocket↔ROS 인터페이스 |
| rosbridge_library | 2.0.5 | rover, base, dashboard | rosbridge 메시지 변환·프로토콜 처리 |
| rosbridge_msgs | 2.0.5 | rover, base, dashboard | rosbridge 내부 메시지 |
| topic_tools | 1.1.1 | rover | 토픽 relay·throttle 등 런타임 중계 |
| rviz2 | 11.2.26 | rover | 로버·센서·지도 3D 시각화 |
| rviz_common | 11.2.26 | rover | RViz 공통 API |
| rviz_default_plugins | 11.2.26 | rover | RViz 기본 표시 플러그인 |
| std_msgs | 4.9.1 | rover, scripts | 기본 상태·수치 메시지 |
| std_srvs | 4.9.1 | rover | Trigger·SetBool 등 공통 서비스 |
| sensor_msgs | 4.9.1 | rover, scripts | 카메라·IMU·joint state·배터리·GNSS 메시지 |
| geometry_msgs | 4.9.1 | rover, scripts | Twist·Pose·Transform 등 제어·좌표 메시지 |
| nav_msgs | 4.9.1 | rover | odometry·경로·지도 메시지 |
| diagnostic_msgs | 4.9.1 | rover, scripts | 시스템 진단·상태·지연 계측 메시지 |
| control_msgs | 4.8.0 | rover, scripts | 관절 제어·trajectory·Servo 관련 메시지 |
| action_msgs | 1.2.2 | rover | action 상태·취소 공통 인터페이스 |
| builtin_interfaces | 1.2.2 | rover | Time·Duration 등 IDL 기본 자료형 |
| geographic_msgs | 1.0.6 | rover | 위도·경도·고도 등 지리 메시지 |
| rcl_interfaces | 1.2.2 | rover, scripts | ROS 파라미터 서비스; scripts의 실험 기록에서 사용 |
| rosidl_runtime_py | 0.9.3 | rover, scripts | Python에서 메시지·서비스 자료형 로딩; 실험 기록에서 사용 |
| ament_cmake | 1.3.14 | rover | ROS CMake 패키지 빌드 |
| ament_cmake_core | 1.3.14 | rover | ament CMake 빌드 핵심 기능 |
| ament_cmake_python | 1.3.14 | rover | CMake 패키지 안의 Python 모듈 설치 |
| rosidl_default_generators | 1.2.1 | rover | 자체 msg/srv/action 코드 생성 |
| rosidl_default_runtime | 1.2.1 | rover | 생성된 메시지 런타임 의존성 |
| ros_environment | 3.2.2 | rover | ROS_VERSION·ROS_DISTRO 환경 설정 |
| ament_cmake_gtest | 1.3.14 | rover | C++ GoogleTest 연동 |
| ament_cmake_pytest | 1.3.14 | rover | CMake에서 Python pytest 실행 |
| ament_cmake_test | 1.3.14 | rover | ament 공통 테스트 실행 지원 |
| ament_lint_auto | 0.12.15 | rover | 패키지 선언 기반 정적 검사 자동 연결 |
| ament_lint_common | 0.12.15 | rover | ament 기본 정적 검사 모음 |
| ament_copyright | 0.12.15 | rover | 소스 라이선스·저작권 검사 |
| ament_flake8 | 0.12.15 | rover | Python 코드 정적 검사 |
| ament_pep257 | 0.12.15 | rover | Python docstring 검사 |
| ament_cmake_copyright | 0.12.15 | rover | CMake의 저작권 검사 연결 |
| ament_cmake_cppcheck | 0.12.15 | rover | CMake의 C/C++ 정적 검사 연결 |
| ament_cmake_uncrustify | 0.12.15 | rover | CMake의 C/C++ 코드 형식 검사 연결 |

### 소스가 참조하지만 현재 ROS 환경에서 발견되지 않은 패키지

| 의존성 이름 | NX 설치 버전 / 상태 | 소속 | 제공 기능 |
| --- | --- | --- | --- |
| ros_gz | **미발견** | rover | Gazebo↔ROS 통합 메타패키지 |
| ros_gz_sim | **미발견** | rover | Gazebo 실행·모델 spawn |
| ros_gz_bridge | **미발견** | rover | Gazebo 메시지와 ROS 메시지 bridge |
| gz_ros2_control | **미발견** | rover | Gazebo의 ros2_control 하드웨어 플러그인 |
| gz_ros2_control_demos | **미발견** | rover | 현재 rover description에서 선언한 Gazebo 제어 예제 패키지 |
| joint_state_publisher | **미발견** | rover | GUI/URDF 기반 joint state 생성 |
| joint_state_publisher_gui | **미발견** | rover | GUI 슬라이더 기반 joint state 생성 |

`ros_gz*`·`gz_ros2_control*` 미발견 상태를 “NX에 Fortress가 설치되어 있다”로 해석하지 않는다.
현재 장비에서 시뮬레이션까지 검증한 기록은 아니다. `ament_python`은 colcon의 Python 빌드 방식으로,
동명의 ROS resource-index 패키지가 없다는 이유로 미설치 항목에 넣지 않았다.
`rtcm_msgs`의 `catkin`, `message_generation`, `message_runtime`은 `ROS_VERSION == 1` 조건부 의존성이므로
ROS 2 설치 대상에서 제외했다.

## 4. rover: NVIDIA·영상·RealSense 네이티브 의존성

| 의존성 이름 | NX 설치 버전 (Debian 전체 버전) | 소속 | 제공 기능 |
| --- | --- | --- | --- |
| nvidia-l4t-core | 36.5.0-20260115194252 | rover | Jetson 사용자 공간 BSP 기준 |
| nvidia-l4t-kernel | 5.15.185-tegra-36.5.0-20260115194252 | rover, scripts | Jetson 커널·장치 드라이버 |
| nvidia-l4t-kernel-dtbs | 5.15.185-tegra-36.5.0-20260115194252 | rover | 캐리어 보드·장치 구성을 위한 device tree |
| nvidia-l4t-bootloader | 36.5.0-20260115194252 | rover | Jetson 부트 펌웨어·부팅 체인 |
| nvidia-l4t-camera | 36.5.0-20260115194252 | rover | NVIDIA 카메라 드라이버·라이브러리 |
| nvidia-l4t-multimedia | 36.5.0-20260115194252 | rover | Jetson 영상 처리·인코딩 런타임 |
| nvidia-l4t-gstreamer | 36.5.0-20260115194252 | rover | NVENC·NVMM·NVIDIA GStreamer 플러그인 |
| cuda-toolkit-12-6 | 12.6.11-1 | rover | CUDA 12.6 툴킷 메타패키지 |
| cuda-nvcc-12-6 | 12.6.68-1 | rover | CUDA 컴파일러; nvcc V12.6.68 |
| cuda-cudart-12-6 | 12.6.68-1 | rover | CUDA 실행 런타임 |
| libcudnn9-cuda-12 | 9.3.0.75-1 | rover | GPU 딥러닝 연산 |
| libgstreamer1.0-dev | 1.20.3-0ubuntu1.1 | rover, base | GStreamer 코어 C/C++ 빌드·파이프라인 |
| libgstreamer-plugins-base1.0-dev | 1.20.1-1ubuntu0.6 | rover, base | GStreamer app/video/rtp API 헤더·링크 |
| gstreamer1.0-tools | 1.20.3-0ubuntu1.1 | rover, base, scripts | gst-launch-1.0·gst-inspect-1.0 |
| gstreamer1.0-plugins-base | 1.20.1-1ubuntu0.6 | rover, base | appsrc/appsink·영상 변환·기본 pipeline 요소 |
| gstreamer1.0-plugins-good | 1.20.3-0ubuntu1.5 | rover, base | V4L2 카메라 소스·RTP H.264 전송/수신·UDP 요소 |
| gstreamer1.0-plugins-bad | 1.20.3-0ubuntu1.1 | rover, base | h264parse·OpenH264 등 영상 처리 요소 |
| gstreamer1.0-plugins-ugly | 1.20.1-1 | rover, base | x264 소프트웨어 H.264 인코더 |
| v4l-utils | 1.22.1-2build1 | rover, scripts | UVC 카메라 포맷·해상도·장치 조회 |

| 의존성 이름 | NX 설치 버전 / 상태 | 소속 | 제공 기능 |
| --- | --- | --- | --- |
| librealsense2 (소스 설치) | 2.57.7; pkg-config·/usr/local/lib 확인 | rover | RealSense RGB-D SDK; apt 밖의 네이티브 라이브러리 |
| nvv4l2h264enc | plugin 1.14.0; L4T 36.5.0 패키지 | rover | NX 하드웨어 H.264 인코딩. 플러그인 발견만 확인 |
| nvvidconv | plugin 1.2.3; L4T 36.5.0 패키지 | rover | NVIDIA 영상 포맷 변환·크기 조절 |
| x264enc | plugin 1.20.1 | rover | 소프트웨어 H.264 인코딩; Nano 후보 경로 |
| openh264enc | plugin 1.20.3 | rover | 추가 소프트웨어 인코더; 설치 상태만 기록 |
| rtph264pay / rtph264depay | plugin 1.20.3 | rover, base | H.264 RTP 패킷화·복원 |
| h264parse / v4l2src | plugin 1.20.3 | rover, base | H.264 파싱 / V4L2 카메라 입력 |
| appsrc / appsink | plugin 1.20.1 | rover, base | 네이티브 코드와 pipeline 사이 버퍼 입출력 |
| TensorRT / Python tensorrt | **미발견**; dpkg에 libnvinfer 계열 없음, Python spec 없음 | rover | 선택적 .engine 모델 빌드·추론. 설치됨으로 가정하지 않음 |
| pyrealsense2 | **미발견** | rover | RealSense Python SDK. 현재 ROS wrapper의 C++ SDK와 별개 |
| gstreamer1.0-libav / avdec_h264 | **미설치 / 플러그인 미발견** | rover, base | libav 기반 H.264 디코딩; 필요 pipeline 여부를 목표 환경에서 판단 |

GStreamer 코어는 `1.20.3`, app/video/rtp **링크 모듈**은 `1.20.1`이다.
`rtph264pay` 같은 런타임 **플러그인**의 `1.20.3`과 구분한다. NVIDIA 플러그인의 자체 버전
`1.14.0`/`1.2.3`을 GStreamer 코어 버전으로 읽으면 안 된다.
`mr2_video_streaming/CMakeLists.txt`는 GStreamer 네 모듈을 직접 요구하지만 현재
`package.xml`에는 GStreamer 개발 패키지가 명시되어 있지 않다. `rosdep install`만으로
빌드 준비가 모두 끝난다고 가정하지 않는다.

RealSense 소스 설치의 버전 근거:

| 외부 소스 | 확인한 버전 / SHA | 소속 | 제공 기능 |
| --- | --- | --- | --- |
| ~/librealsense | `9a0dd70db1a2c180b69c6c257cd2ee6120505499` | rover | 로컬 SDK 소스 체크아웃. 설치 라이브러리 버전은 2.57.7이며, 이 SHA로 빌드했다는 이력까지 확인한 것은 아님 |
| ~/ros2_ws/src/realsense-ros | 4.57.7 / `5c2244ca5cd9867c9ee63769668891430f460dfd` | rover | NX가 사용하는 외부 ROS wrapper 소스. 설치 prefix의 버전도 4.57.7 |
| ~/jetson-orin-librealsense | `dcdd8ce11f26a6263c3a2b3d6498426ff7d4ea64` | rover, scripts | Jetson용 SDK 설치 도우미 소스. 기존 파일의 존재를 기록했으며 실행·적용 이력은 미확인 |

## 5. rover: Python 의존성

아래는 source한 ROS 환경의 `/usr/bin/python3` 기준이다. 사용자 설치 경로
`~/.local/lib/python3.10/site-packages`가 `/usr/lib/python3/dist-packages`보다 먼저 해석된다.
Python 배포 패키지명과 import명은 다를 수 있다. `opencv-python`은 `cv2`, `PyYAML`은 `yaml`로 import한다.

| 의존성 이름 | NX 설치 버전 | 소속 | 제공 기능 | 해석되는 설치 위치 |
| --- | --- | --- | --- | --- |
| numpy | 1.26.4 | rover | RGB-D·좌표·분광 데이터 수치 연산 | 사용자 ~/.local |
| opencv-python | 4.13.0.92 | rover | Python 영상 처리·카메라·YOLO 전후처리; import cv2=4.13.0 | 사용자 ~/.local |
| ultralytics | 8.4.46 | rover | YOLO 모델 로딩·검출·학습/내보내기 라이브러리 | 사용자 ~/.local |
| torch | 2.8.0 | rover | YOLO 딥러닝 텐서·추론; import 2.8.0, 빌드 CUDA=12.6 | 사용자 ~/.local |
| torchvision | 0.23.0 | rover | PyTorch 비전 연산·모델 도구; 설치 메타데이터 확인 | 사용자 ~/.local |
| ultralytics-thop | 2.0.19 | rover | Ultralytics의 모델 연산량 분석 전이 의존성 | 사용자 ~/.local |
| psutil | 5.9.0 | rover | CPU·메모리·디스크 등 시스템 상태 수집 | 시스템 /usr/lib |
| PyYAML | 5.4.1 | rover | launch·MoveIt·카메라 YAML 설정 파싱 | 시스템 /usr/lib |
| scipy | 1.8.0 | rover | Ultralytics/비전 수치 연산 전이 의존성 | 시스템 /usr/lib |
| pillow | 12.2.0 | rover | Ultralytics 이미지 읽기·쓰기 전이 의존성 | 사용자 ~/.local |
| matplotlib | 3.5.1 | rover | Ultralytics 결과 plotting 전이 의존성 | 시스템 /usr/lib |
| requests | 2.25.1 | rover | Ultralytics HTTP 리소스 접근 전이 의존성 | 시스템 /usr/lib |
| polars | 1.40.1 | rover | Ultralytics 테이블 처리 전이 의존성 | 사용자 ~/.local |
| polars-runtime-32 | 1.40.1 | rover | Polars arm64 네이티브 실행 모듈 | 사용자 ~/.local |
| pytest | 6.2.5 | rover, scripts | Python 단위·통합 테스트 실행 | 시스템 /usr/lib |
| setuptools | 59.6.0 | rover | ROS Python 패키지 빌드·설치 | 시스템 /usr/lib |
| pip | 26.1 | rover, scripts | Python 패키지 설치기; 사용자 pip가 시스템 pip보다 우선 | 사용자 ~/.local |

| 의존성 이름 | NX 설치 버전 (Debian 전체 버전) | 소속 | 제공 기능 |
| --- | --- | --- | --- |
| python3-numpy | 1:1.21.5-1ubuntu22.04.1 | rover | 시스템 NumPy 1.21.5; 사용자 NumPy 1.26.4에 가려짐 |
| python3-opencv | 4.5.4+dfsg-9ubuntu4 | rover | 시스템 Python OpenCV 4.5.4; 사용자 cv2 4.13.0에 가려짐 |
| python3-psutil | 5.9.0-1build1 | rover | 시스템 상태 수집 Python 패키지 |
| python3-yaml | 5.4.1-1ubuntu1 | rover | YAML Python 패키지 |
| python3-pip | 22.0.2+dfsg-1ubuntu0.7 | rover, scripts | 시스템 pip 22.0.2; 사용자 pip 26.1이 우선 |
| python3-pytest | 6.2.5-1ubuntu2 | rover, scripts | 시스템 pytest 실행 패키지 |

이번에 `numpy`, `cv2`, `psutil`, `yaml`, `torch`의 import와 버전 조회는 성공했다.
GPU 추론·모델 로딩·TensorRT 엔진 생성·카메라 동작은 시험하지 않았다.
`opencv-python 4.13.0.92`의 설치 메타데이터는 Python ≥3.9에서 `numpy>=2`를 요구하지만
현재 해석되는 NumPy는 `1.26.4`다. import 성공과 별개로 이 요구사항 불일치를 기록해 둔다.
24.04에서 현재 pip 설치 조합을 그대로 복사하기 전에 해결해야 한다.

직접 import하는 Ultralytics와 GPU 관련 의존성에는 현재 `requirements.txt` 또는 버전 lock이 없다.
전이 Python 패키지 전체 버전·중복 설치 위치와 관련 패키지의 Requires-Dist는
[Python 목록](orin-nx-2026-11-17/python-distributions.json)과
[네이티브 조회 결과](orin-nx-2026-11-17/native-details.json)에 기록했다.

## 6. base: gateway npm 의존성

`package.json`의 범위와 `package-lock.json`의 확정 버전을 분리했다.
아래 gateway/dashboard **직접 의존성 28개의 node_modules 설치 버전은 모두 lockfile과 일치**한다.
이는 네이티브 모듈 로딩이나 실제 ROS relay의 동작을 검증한 결과는 아니다.

| 의존성 이름 | NX 설치 = lock 버전 | package.json 요구 범위 | 소속 | 제공 기능 |
| --- | --- | --- | --- | --- |
| dotenv | 16.6.1 | ^16.4.7 | base | 저장소 환경 설정 파일을 gateway 실행 환경에 로딩 |
| rclnodejs | 1.8.1 | ^1.5.0 | base | Node.js ROS 2 클라이언트; base GNSS/RTCM ROS 토픽 relay |
| serialport | 12.0.0 | ^12.0.0 | base | XBEE·안테나 장치 직렬 통신 |
| ws | 8.18.3 | ^8.16.0 | base | gateway WebSocket 서버·rosbridge proxy·영상/명령·상태 전달 |

`rclnodejs`와 `serialport`는 네이티브 바이너리 의존성이 있으므로 Ubuntu·ROS·Node ABI가 바뀌면
목표 환경에서 다시 설치/빌드하고 실제 relay·serial 동작을 확인한다. `rover-direct` 구성은
base GNSS/RTCM relay를 비활성화하므로 해당 모드가 켜진다고 실제 rclnodejs relay가 검증되는 것은 아니다.

추가 선택 기능인 MAVLink relay는 `mavproxy.py`를 외부 프로세스로 실행한다.
현재 NX에서는 `mavproxy.py`와 Python `MAVProxy`·`pymavlink`를 발견하지 못했다.
이는 `base`의 선택 의존성이며 현재 rover-direct에서 비활성화되는 기능이다.

## 7. dashboard: 웹 런타임·빌드 의존성

### 브라우저 애플리케이션 의존성

| 의존성 이름 | NX 설치 = lock 버전 | package.json 요구 범위 | 소속 | 제공 기능 |
| --- | --- | --- | --- | --- |
| @fontsource/dm-mono | 5.2.7 | ^5.2.7 | dashboard | 고정폭 DM Mono 웹폰트 |
| @fontsource/space-grotesk | 5.2.10 | ^5.2.10 | dashboard | Space Grotesk 웹폰트 |
| bootstrap | 5.3.8 | ^5.3.3 | dashboard | UI CSS·레이아웃·컴포넌트 스타일 |
| maplibre-gl | 4.7.1 | ^4.7.1 | dashboard | 지도 표시·지도 상호작용 |
| react | 18.3.1 | ^18.3.1 | dashboard | 상태 기반 UI 컴포넌트 |
| react-bootstrap | 2.10.10 | ^2.10.10 | dashboard | React Bootstrap UI 컴포넌트 |
| react-dom | 18.3.1 | ^18.3.1 | dashboard | React 브라우저 DOM 렌더링 |
| react-icons | 5.5.0 | ^5.5.0 | dashboard | UI 아이콘 |
| roslib | 1.4.1 | ^1.4.0 | dashboard | 브라우저↔rosbridge ROS 토픽·서비스 통신 |
| three | 0.184.0 | ^0.184.0 | dashboard | 3D 장면·로버/arm 시각화 |
| uplot | 1.6.32 | ^1.6.32 | dashboard | 상태·지연 등 시계열 그래프 |

### 개발·빌드·정적 검사 의존성 (devDependencies)

| 의존성 이름 | NX 설치 = lock 버전 | package.json 요구 범위 | 소속 | 제공 기능 |
| --- | --- | --- | --- | --- |
| @eslint/js | 9.39.2 | ^9.39.1 | dashboard | ESLint JavaScript 기본 규칙 |
| @types/react | 18.3.27 | ^18.3.3 | dashboard | React TypeScript 타입 |
| @types/react-dom | 18.3.7 | ^18.3.0 | dashboard | React DOM TypeScript 타입 |
| @types/three | 0.184.1 | ^0.184.1 | dashboard | Three.js TypeScript 타입 |
| @typescript-eslint/eslint-plugin | 8.51.0 | ^8.44.0 | dashboard | TypeScript ESLint 규칙 |
| @typescript-eslint/parser | 8.51.0 | ^8.44.0 | dashboard | ESLint의 TypeScript 구문 분석 |
| @vitejs/plugin-react | 5.1.2 | ^5.1.1 | dashboard | Vite React JSX 변환·개발 갱신 |
| eslint | 9.39.2 | ^9.39.1 | dashboard | 코드 정적 검사 |
| eslint-plugin-react-hooks | 7.0.1 | ^7.0.1 | dashboard | React Hooks 규칙 검사 |
| eslint-plugin-react-refresh | 0.4.26 | ^0.4.24 | dashboard | React Refresh 호환 코드 검사 |
| globals | 16.5.0 | ^16.5.0 | dashboard | ESLint용 전역 객체 목록 |
| typescript | 5.9.3 | ^5.8.3 | dashboard | 타입 검사·TypeScript 컴파일 도구 |
| vite | 7.3.0 | ^7.2.4 | dashboard | 웹 개발 서버·프로덕션 번들 빌드 |

`esbuild`는 직접 devDependency에 선언되어 있지 않지만 `test:latency` 스크립트가 CLI를 사용한다.
현재 lockfile과 설치 트리에서 아래 전이 버전을 확인했다.

| 의존성 이름 | NX 설치 버전 | 소속 | 제공 기능 |
| --- | --- | --- | --- |
| esbuild | 0.27.2 | dashboard | 번들러 / arm64 네이티브 번들러 바이너리; lock=0.27.2 |
| @esbuild/linux-arm64 | 0.27.2 | dashboard | 번들러 / arm64 네이티브 번들러 바이너리; lock=0.27.2 |
| rollup | 4.54.0 | dashboard | 번들러 / arm64 네이티브 번들러 바이너리; lock=4.54.0 |
| @rollup/rollup-linux-arm64-gnu | 4.54.0 | dashboard | 번들러 / arm64 네이티브 번들러 바이너리; lock=4.54.0 |

근거: [gateway package.json](../../base/gateway/package.json),
[gateway lockfile](../../base/gateway/package-lock.json),
[dashboard package.json](../../dashboard/package.json),
[dashboard lockfile](../../dashboard/package-lock.json),
[직접 의존성 대조 결과](orin-nx-2026-11-17/npm-direct.json).
간접 npm 패키지는 각 lockfile 및 원자료의 전체 lock 버전 목록을 사용한다.

## 8. scripts 및 배포·운용 서비스 의존성

| 의존성 이름 | NX 설치 버전 (Debian 전체 버전) | 소속 | 제공 기능 |
| --- | --- | --- | --- |
| bash | 5.1-6ubuntu1.1 | scripts | 설치·launch wrapper·CAN 진단 셸 실행 |
| coreutils | 8.32-4.1ubuntu1.3 | scripts | timeout·파일 복사·경로 처리 등 기본 명령 |
| sed | 4.8-1ubuntu2 | scripts | 배포 템플릿·로그 편집 |
| perl | 5.34.0-3ubuntu1.5 | scripts | CAN 명령 관측 스크립트의 스트림 파싱 |
| git | 1:2.34.1-1ubuntu1.17 | scripts, rover | 소스·서브모듈 버전 관리 |
| nginx | 1.18.0-6ubuntu14.16 | base, dashboard, scripts | 정적 dashboard·HTTPS·WebSocket reverse proxy |
| openssl | 3.0.2-0ubuntu1.23 | base, dashboard, scripts | HTTPS 인증서 생성·검사 |
| rsync | 3.2.7-0ubuntu0.22.04.4 | dashboard, scripts | 빌드된 dashboard를 배포 경로로 복사 |
| socat | 1.7.4.1-3ubuntu4 | base, rover, scripts | 가상 serial PTY·XBEE 연결/시뮬레이션 |
| network-manager | 1.36.6-0ubuntu2.4 | rover, scripts | nmcli 기반 rover AP·필드 유선 네트워크 구성 |
| chrony | 4.2-2ubuntu2 | base, rover, scripts | chronyd/chronyc 시간 동기화·오프라인 clock 정책·지연 기준 시계 |
| can-utils | 2020.11.0-1 | rover, scripts | candump/cansend/canbusload 등 SocketCAN 진단 |
| iproute2 | 5.15.0-1ubuntu2 | rover, scripts | CAN bitrate·링크·IP 설정/조회 |
| udev | 249.11-0ubuntu3.20 | rover, scripts | USB serial·카메라 별칭·장치 권한 규칙 적용 |
| systemd | 249.11-0ubuntu3.22 | rover, scripts | 자동 시작·clock 서비스·서비스 상태 관리 |
| kmod | 29-1ubuntu1.1 | rover, scripts | SocketCAN/vcan 등 커널 모듈 로딩 도구 |
| tcpdump | 4.99.1-3ubuntu0.2 | base, rover, scripts | RTP/UDP 패킷 캡처·지연/신선도 계측 |
| libcap2-bin | 1:2.44-1ubuntu0.22.04.3 | base, rover, scripts | tcpdump의 getcap/setcap 권한 관리 |
| curl | 7.81.0-1ubuntu1.23 | scripts | HTTP gateway health 확인·계측 준비 |
| iputils-ping | 3:20211215-1ubuntu0.1 | scripts | 필드 네트워크 peer 연결 확인 |

`scripts/record_arm_experiment.py`는 표 3의 rclpy·ROS 메시지와 Python 표준 라이브러리를 사용한다.
`scripts/latency`의 PCAP/RTP 분석·clock 정책은 주로 표준 라이브러리와 외부 `tcpdump`/`chrony` 명령을 사용한다.
소스 근거: [rover-direct 설치기](../../scripts/install_rover_direct.bash),
[AP 설정](../../scripts/configure_rover_ap.bash),
[필드 설정](../../scripts/field_setup.py),
[capture 권한 설치기](../../scripts/latency/install_capture_permissions.bash),
[영상 빌드 설정](../../rover/ros2_ws/src/mr2_video_streaming/CMakeLists.txt).

## 9. 저장소 내부 의존성과 버전 고정된 외부 소스

아래 항목은 apt/pip로 다시 설치할 외부 라이브러리가 아니라 함께 빌드할 ROS 소스다.
버전 칸은 현재 소스 선언 / 활성 설치 prefix의 패키지 버전이다. `0.0.0` 같은 값만으로 변경 이력을
구분할 수 없으므로 표 1의 SHA와 수정 상태를 함께 보존해야 한다.

| 의존성 이름 | 소스 선언 / 설치 버전 | 소속 | 제공 기능 |
| --- | --- | --- | --- |
| mr2_action_interface | 0.0.0 / 0.0.0 | rover | 로버 명령·임무·장치 공통 msg/action 인터페이스 |
| mr2_battery_monitor | 0.1.0 / 0.1.0 | rover | CAN 배터리 상태 수집·ROS 발행 |
| mr2_camera_turret | 0.1.0 / 0.1.0 | rover | CAN 카메라 turret 제어 |
| mr2_can_bus_core | 0.0.0 / 0.0.0 | rover | CAN 버스·장치·프로토콜 공통 추상화 |
| mr2_can_hardware_interface | 0.1.0 / 0.1.0 | rover | ros2_control과 CAN 장치 연결 |
| mr2_control | 0.1.0 / 0.1.0 | rover | 장치 driver·제어 mode·안전 상태 관리 |
| mr2_devices_ak_servo | 0.0.0 / 0.0.0 | rover | AK servo 장치 plugin |
| mr2_devices_output_actuator | 0.0.0 / 0.0.0 | rover | 출력 actuator·custom actuator plugin |
| mr2_devices_robstride | 0.1.0 / 0.1.0 | rover | RobStride 모터 plugin |
| mr2_flat_motor | 0.1.0 / 0.1.0 | rover | flat rover 모터 제어·상태 처리 |
| mr2_latency_diagnostics | 0.1.0 / 0.1.0 | rover | 로버 측 지연 시험·캡처·결과 업로드 |
| mr2_latency_msgs | 0.1.0 / 0.1.0 | rover | 지연 계측 msg/srv 인터페이스 |
| mr2_launch | 0.0.0 / 0.0.0 | rover | 운용 profile·장치·서비스 통합 launch |
| mr2_led | 0.1.0 / 0.1.0 | rover | CAN LED 장치 제어 |
| mr2_moveit | 0.3.0 / 0.3.0 | rover | 매니퓰레이터 MoveIt·Servo 설정과 launch |
| mr2_nav2_plugins | 0.0.0 / 0.0.0 | rover | traversability costmap layer 등 자체 Nav2 plugin |
| mr2_panorama | 0.0.0 / 0.0.0 | rover | panorama 촬영·영상 결합 |
| mr2_rover_auto | 0.0.0 / 0.0.0 | rover | 자율 주행·임무 action·지형 처리 |
| mr2_rover_control | 0.0.0 / 0.0.0 | rover | 로버 drive·steering ros2_control 컨트롤러 |
| mr2_rover_description | 0.0.0 / 0.0.0 | rover | URDF·xacro·실기/시뮬레이션 모델·controller 설정 |
| mr2_science_module | 0.1.0 / 0.1.0 | rover | CAN 과학 모듈 제어·서비스 |
| mr2_spectrophotometer | 0.1.0 / 0.1.0 | rover | 분광 영상 수집·보정·측정 |
| mr2_spectrophotometer_msgs | 0.1.0 / 0.1.0 | rover | 분광 측정 메시지 |
| mr2_system_status | 0.0.1 / 0.0.1 | rover | 자원 사용·온도·clock 상태 진단 |
| mr2_video_streaming | 0.1.0 / 0.1.0 | rover | 영상 캡처·인코딩·RTP 송수신·지연/신선도 제어 |
| mr2_xbee_bridge | 0.1.0 / 0.1.0 | rover | XBEE 프레임과 ROS 명령·상태 사이 변환 |
| mr2_yolo_perception | 0.0.0 / 0.0.0 | rover | YOLO RGB-D 검출·객체 3D 위치 산출 |
| mr2_flat_bench | 0.1.0 / 주 조사 overlay에서 미발견 | rover | 분리된 flat bench 워크스페이스의 모터 시험 도구 |

| 의존성 이름 | 소스·설치 버전 / 소스 SHA | 소속 | 제공 기능 |
| --- | --- | --- | --- |
| rtcm_msgs | 1.1.6 / `8d27ab5f4d4a581e1bb9c87d572994c25197f6d4` | rover, base | GNSS RTCM 보정 메시지; git submodule |
| ublox_dgnss | 0.7.4 / `f4fdcf77f1427ef92195e064e42232855f016256` | rover, base | GNSS stack 메타패키지; git submodule |
| ublox_dgnss_node | 0.7.4 / 위 ublox_dgnss SHA | rover, base | u-blox USB GNSS receiver 드라이버 |
| ublox_ubx_msgs | 0.7.4 / 위 ublox_dgnss SHA | rover, base | UBX GNSS 메시지 |
| ublox_ubx_interfaces | 0.7.4 / 위 ublox_dgnss SHA | rover | u-blox 장치 서비스 인터페이스 |
| ublox_nav_sat_fix_hp_node | 0.7.4 / 위 ublox_dgnss SHA | rover, base | 고정밀 GNSS 데이터를 NavSatFix로 변환 |
| ntrip_client_node | 0.7.4 / 위 ublox_dgnss SHA | rover, base | NTRIP caster에서 RTCM 보정 수신 |

`mr2_flat_bench`는 `rover/flat_bench_ws`의 소스이며 주 조사 overlay에 포함되지 않았다.
별도 bench install의 존재·동작을 이 표의 미발견 상태로 단정하지 않는다.
`base/arduino`와 `rover/firmware`의 MCU SDK·Arduino core·보드 firmware 버전은 NX 호스트 런타임 의존성 범위와
구분한다. 이번 조사는 MCU에 flash된 버전이나 해당 보드의 빌드 도구 설치 상태까지 확인하지 않았다.

## 10. Ubuntu 24.04 버전 선정 전에 확인할 사항

| 대상 | 현재 기준에서 확인한 사항 | 다음 버전 선정·설치 절차에 반영할 내용 |
| --- | --- | --- |
| BSP·커널·CUDA·cuDNN | L4T 36.5.0 / 커널 5.15.185-tegra / CUDA 12.6 / cuDNN 9.3.0.75 | 새 BSP·캐리어 보드 지원에 맞춰 함께 선정. 현재 Jammy 패키지 버전을 Noble 설치 명령에 그대로 사용하지 않음 |
| ROS 제어·Servo·Nav2 | Humble ros2_control 2.53.1 / controllers 2.52.1 / MoveIt 2.5.9 / Nav2 1.1.20 | Jazzy의 실제 설치 버전을 새로 기록하고 API·설정 차이를 포팅·검증 |
| 시뮬레이션 | 현재 NX에서 ros_gz·gz_ros2_control 미발견 | Harmonic 연동 패키지·센서·clock·controller 설치와 시험을 새 기준으로 정의 |
| RealSense | 외부 underlay wrapper 4.57.7 + /usr/local SDK 2.57.7 | 외부 워크스페이스·SDK 빌드 옵션·udev 규칙까지 설치 절차에 포함 |
| Python 영상·추론 | 사용자 pip가 apt NumPy/OpenCV를 가림; opencv-python의 NumPy 요구사항 불일치 | 목표 Python과 GPU wheel 조합·NumPy/OpenCV를 검증하고 requirements/constraints로 고정 |
| TensorRT | 현재 조사 경로에서 TensorRT 미발견; 저장소에는 yolov11.pt 존재 | 필수 모델 포맷을 정하고, .engine을 사용할 경우 대상 NX에서 생성·검증한 절차·버전·checksum 기록 |
| 영상 전송 | GStreamer 개발 패키지가 package.xml에 완전히 표현되지 않음; NVIDIA 인코더 plugin은 발견 | apt 설치 항목을 명시하고 새 BSP에서 plugin 제공·실제 인코딩을 확인. Nano SW 인코딩과 NX HW 인코딩 결과는 별도 기록 |
| Node native 모듈 | Node 24.15.0 / ABI 137, rclnodejs 1.8.1·serialport 12.0.0 | lockfile을 기준으로 목표 환경에서 재설치·재빌드하고 실제 ROS·serial 경로 검증 |
| 선택 운용 기능 | MAVProxy 미발견; rover-direct는 GNSS relay·autonomy·science 등 비활성화 | 첫 릴리스에 필요한 기능을 정한 뒤 의존성을 필수/선택으로 구분 |
| 재현성 | 작업 트리에 수정·미추적 파일이 있으며 설치 바이너리와 소스의 일치 이력은 미확인 | 이 의존성 표와 별도로 실제 배포 소스·설정·보정값·모델·복구 이미지 manifest를 보존 |

이 문서의 완료 범위는 **현재 의존성 목록과 버전의 기록**이다. 클린 재설치·빌드·기능 검증이나
GU-00 전체 완료를 뜻하지 않는다. 목표 환경의 설치 버전은 새 환경 수집 결과를 나란히 추가해 확정한다.

## 11. 원자료와 재확인 방법

| 파일 | 내용 |
| --- | --- |
| [metadata.json](orin-nx-2026-11-17/metadata.json) | 조사 시각·장비·OS·BSP·소스 SHA·작업 트리 상태·선언 파일 SHA-256 |
| [dpkg-installed.tsv](orin-nx-2026-11-17/dpkg-installed.tsv) | NX에 installed 상태인 전체 Debian 패키지 이름·버전·아키텍처 |
| [ros-packages.json](orin-nx-2026-11-17/ros-packages.json) | 활성 ROS 패키지 전체 버전·prefix, 소스 manifest 의존성·사용 패키지, launch/CMake 참조 |
| [python-distributions.json](orin-nx-2026-11-17/python-distributions.json) | ROS source 후 Python 배포 패키지 전체 버전·설치 위치·핵심 import 경로 |
| [native-details.json](orin-nx-2026-11-17/native-details.json) | 핵심 Python import 결과·CUDA 빌드 버전·GStreamer plugin·pkg-config·RealSense SHA·Python 요구사항 메타데이터 |
| [npm-direct.json](orin-nx-2026-11-17/npm-direct.json) | 직접 npm 의존성의 요구 범위·lock·node_modules 버전 비교 |
| [gateway-npm-lock-versions.json](orin-nx-2026-11-17/gateway-npm-lock-versions.json) | gateway lockfile 전체 패키지 경로·버전 |
| [dashboard-npm-lock-versions.json](orin-nx-2026-11-17/dashboard-npm-lock-versions.json) | dashboard lockfile 전체 패키지 경로·버전 |
| [SHA256SUMS](orin-nx-2026-11-17/SHA256SUMS) | 이 조사 원자료 파일들의 무결성 확인용 SHA-256 |

조회에 사용한 핵심 명령은 다음과 같다. ROS/Python은 아래 source 순서를 유지한다.

```bash
cat /etc/os-release
cat /etc/nv_tegra_release
uname -r
dpkg-query -W -f='${binary:Package}\t${Version}\t${Architecture}\t${db:Status-Status}\n'
node --version
npm --version
node -p 'process.versions.modules'
git rev-parse HEAD
git status --short
git submodule status

source /opt/ros/humble/setup.bash
source rover/ros2_ws/install/setup.bash
ros2 pkg prefix realsense2_camera
python3 --version
python3 -c 'import numpy, cv2, torch; print(numpy.__version__, cv2.__version__, torch.__version__, torch.version.cuda)'
pkg-config --modversion realsense2 opencv4 gstreamer-1.0 gstreamer-app-1.0 gstreamer-video-1.0 gstreamer-rtp-1.0
gst-inspect-1.0 nvv4l2h264enc
```

원자료에는 환경변수 전체, `.env` 내용, 계정 credential, 인증서 개인키를 포함하지 않았다.
향후 재수집 시에는 날짜별 디렉터리를 새로 만들고 이 기준 자료를 덮어쓰지 않는다.

