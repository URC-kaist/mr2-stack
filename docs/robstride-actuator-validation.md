# 구동 모터 드라이버 교체 구현 및 가상 검증

검증일: 2026-11-12. 저장소: `/home/mr2/mr2-stack`.
환경: aarch64, ROS 2 Humble. 실물 CAN 및 모터는 구동하지 않았다.

이 문서는 최초 소프트웨어 검증 기록이다. 이후 같은 날 RS-06 한 대의
[실기 정·역회전 및 정지 검증](robstride-hardware-validation.md)을 추가로 수행해 통과했다.

## 구현 결과

- 기존 클래스를 `CustomActuatorDevice`로 변경하고 소스·라이브러리·플러그인
  이름을 갱신했다. `mr2_devices_output_actuator` 패키지와 메시지 타입·진단
  토픽은 유지했다. 이전 플러그인 식별자는 새 식별자로 교체했다.
- `mr2_devices_robstride/RobstrideDevice`를 추가했다. 기존 `CanDevice` API와
  `CanBusManager` 송수신 경로를 사용한다. 독립 ROS 노드는 추가하지 않았다.
- `actuator:=custom|rs`를 real/direct → rover → description real → URDF →
  ros2_control xacro로 전달한다. 기본값은 custom이며 잘못된 값은 거부한다.
- rs는 wheel joint 4개의 플러그인과 모터 파라미터만 바꾼다. 조향과 그리퍼,
  transmission, 상위 velocity 명령(rad/s)은 유지한다.
- RS 기동 시 stop → 속도 모드 설정 및 readback → 제한값과 zero 설정 →
  enable 상태를 확인한다. 순차적인 다른 장치의 기동이 끝난 뒤, 첫 write
  주기에서 watchdog과 새 zero 피드백을 확인하고 속도 명령 전송을 시작한다.
- 고장·피드백 타임아웃·예상하지 않은 reset은 모터별 정지를 유지한다.
  피드백이 복구돼도 자동 enable하지 않는다. 명시적인 재활성화가 필요하다.
- CanHW에서 뒤쪽 장치의 기동이 실패하면 이미 활성화된 장치도 정지시킨다.
- 연속 회전 위치를 누적하며, 회전 수를 판별할 수 없는 피드백 공백은 위치를
  무효화한다. 기동 세션 사이의 회전 수는 복원하지 않는다.

## 검증 결과

관련 8개 패키지를 격리된 `/tmp/mr2-rs-validation` 환경과 기존 작업공간
`rover/ros2_ws/{build,install}` 양쪽에서 빌드했다. 두 빌드 모두 성공했다.
기존 작업공간에 새 플러그인 라이브러리와 launch 파일 설치까지 완료했다.

| 기능 검증 | 테스트 수 | 결과 |
| --- | ---: | --- |
| RS-06 가상 CAN 모터: 바이트 인코딩·디코딩, 기동 실패, 제한값, 고장·timeout, wrap, 동시 수신 | 12 | 통과 |
| 실제 pluginlib 혼합 로딩 및 장치별 독립 명령 메모리 | 1 | 통과 |
| CanHW 부분 기동 실패 시 전체 장치 정지 | 1 | 통과 |
| actuator launch/xacro: 기본값·custom·rs, 두 backend, real/direct 경로, 잘못된 값 | 31 | 통과 |
| 기존 launch 프로파일 회귀 검증 | 30 | 통과 |
| 기존 mr2_control 드라이버·변환·주행·스케줄링·launch 회귀 검증 | 27 | 통과 |
| 합계 | 102 | 실패 0 |

실제 `ros2 launch ... --show-args`에서도 real/direct 양쪽의 허용값과 custom
기본값을 확인했다. `git diff --check`도 통과했다. 원래 존재하던 다른 작업의
변경사항은 보존했다.

추가로 실행한 `mr2_rover_control`의 `uncrustify` 검사 1건은 헤더·소스
2개 파일의 기존 형식 문제로 실패했다. 두 파일이 검증 당시 HEAD와 동일한
것을 확인했으며 이 작업에서는 수정하지 않았다. 빌드의 mr2_control 테스트
번역 단위에는 기존 소스 직접 include 방식에 따른 anonymous namespace
연결 경고가 있으나 빌드 및 기능 검증은 성공했다.

결과 XML, 테스트 집계, launch 인자 출력과 스타일 검사 로그는
[`validation/robstride-2026-11-12`](validation/robstride-2026-11-12)에 보관했다.

## 재검증 및 사용

```bash
cd /home/mr2/mr2-stack/rover/ros2_ws
source /opt/ros/humble/setup.bash
source install/local_setup.bash

colcon build --symlink-install --packages-select \
  mr2_can_bus_core mr2_devices_output_actuator mr2_devices_robstride \
  mr2_can_hardware_interface mr2_control mr2_rover_control \
  mr2_rover_description mr2_launch --cmake-args -DBUILD_TESTING=ON

source install/local_setup.bash
colcon test --packages-select mr2_devices_robstride \
  mr2_can_hardware_interface mr2_control --return-code-on-test-failure

PYTHONPATH="$PWD/src/mr2_launch:$PWD/src/mr2_rover_description:$PYTHONPATH" \
  python3 -m pytest src/mr2_launch/test/test_actuator_selection.py \
  src/mr2_launch/test/test_profiles.py -q
```

실물 운용 명령은 다음과 같다. 검증 과정에서는 실행하지 않았다.

```bash
ros2 launch mr2_launch rover_real.launch.py actuator:=rs
ros2 launch mr2_launch rover_direct.launch.py actuator:=rs
```

가상 검증은 메모리 안의 CAN 프레임 교환과 실제 설치 플러그인 로딩으로
수행했다. 커널 vcan/SocketCAN 실물 송수신, 실제 드라이버 펌웨어와 기계적
장착 상태는 검증 범위에 포함되지 않는다.

구현 프로토콜은 [RS-06 제조사 매뉴얼 251112](https://files.seeedstudio.com/products/RobStride/Product%20Literature/RS06/RS06User%20Manual251112.pdf)의
private CAN이다. 실기 전에 펌웨어 프로토콜·피드백 스케일·각도 순환 범위,
모터 ID, 공유 버스 속도, 출력축 기준 방향과 제한값을 확인해야 한다.
`scripts/can0.bash`와 CAN 속도 설정은 변경하지 않았다. 장치별 파라미터와
기동·정지 동작은 [플러그인 README](../rover/ros2_ws/src/mr2_devices_robstride/README.md)에 설명했다.
