# 기존 로버 제어 스택을 사용하는 flat 시험 결과

2026-11-13, `/home/mr2/mr2-stack`, ROS 2 Humble.

실제 RS-06 한 대와 모의 모터 일곱 대로 기존 8개 구동·조향 인터페이스를
구성했다. 기존 주행 컨트롤러, ros2_control, CanHW를 통한 모의 및 저속
실기 시험을 통과했다. 브라우저와 USB 게임패드의 실제 조작은 자동 시험에
포함하지 않았다.

## 기존 소프트웨어 보존

이번 flat 구성 작업의 변경 범위는 새 `rover/flat_bench_ws`뿐이다.
기존 작업 및 사용자 변경 사항은 유지했다. 보존 기준 시점은
`production-before.json`의 2026-11-13 00:11:43 KST이다.
앞서 승인받아 구현했던 RS 드라이버 변경 사항까지 되돌린다는 의미는 아니다.

`production-integrity.json`에서 다음을 SHA-256으로 비교했다.

- 기존 rover 소스, base, dashboard, scripts 파일 665개: 변경·추가 없음.
- 설치된 CanHW, 주행 컨트롤러, RobstrideDevice 라이브러리와 컨트롤러 YAML
  네 파일: 변경 없음.
- 기존 flat 프로필과 저장된 프로필 옵션: 유지.

기존 워크스페이스를 다시 빌드하지 않았다. 새 워크스페이스만 빌드했으며,
기존 전체 로버의 launch, xacro, 컨트롤러 YAML에 시험 옵션을 추가하지 않았다.

## 실행 구조

```mermaid
flowchart LR
  D[기존 대시보드 WebSocket] --> G[기존 gateway / XBee bridge]
  G --> T[/base/cmd_vel]
  T --> C[기존 TwistToCommandsController]
  C --> M[기존 ros2_control / CanHW]
  M --> R[기존 RobstrideDevice: 실제 FL]
  M --> V[새 MockCanDevice: 나머지 7개]
  R --> B[기존 CanBusManager / can0]
  B --> RS[RS-06 ID 127]
```

`controller_manager/list_controllers`에서 기존
`mr2_rover_control/TwistToCommandsController`와 `joint_state_broadcaster`가
active임을 확인했다. `list_hardware_components`에서도
`mr2_can_hardware_interface/CanHW`가 active였다.
CanHW에는 8개 joint, 8개 actuator, 8개 transmission이 그대로 등록된다.

설치된 기존 xacro를 전개한 후 메모리에서 장치 선택만 바꾼다.
기존 로봇 형상, command/state interface, transmission, 컨트롤러 YAML 및
100 Hz 설정은 재사용한다. 조향 네 개는 position, 구동 네 개는 velocity이다.
그리퍼와 자율주행 모듈은 기존 비활성화 옵션을 사용한다.

실제 FL만 기존 RS 플러그인을 사용하고, 나머지 구동 세 개와 조향 네 개는
별도 패키지의 CanDevice 플러그인을 사용한다. 모의 장치는 속도 적분과
위치 추종을 제공하며 CAN 소켓을 열거나 CAN 프레임을 보내지 않는다.
모터 펌웨어, 관성, 부하, 실제 조향 장치의 고장 등을 재현하는 시뮬레이터는 아니다.
대시보드 입력을 직접 모터로 보내는 별도 모터 ROS 노드는 없다.

실제 장치의 시험용 설정은 ID 127, host 253, can0 500 kbps,
속도 상한 0.2 rad/s, 전류 제한 1 A, 가속도 0.5 rad/s²이다.
FL의 기존 transmission -1.0을 유지했다. 전진 시 모터축 속도와 joint 속도의
부호가 반대인 것은 이 변환에 따른다.

## 검증 결과

| 시험 | 결과 | 증거 |
| --- | --- | --- |
| 새 패키지 빌드 | 통과 | `build-summary.txt` |
| 모의 장치 단위 시험 | 4개 통과 | `unit/test_mock.gtest.xml` |
| joint 선택·기존 설정 보존 시험 | 9개 통과 | `unit/test_description.xunit.xml` |
| 8개 모의 장치 + 기존 제어 스택 | 통과 | `all-mock/summary.json` |
| 실제 FL + 모의 7개 + 기존 제어 스택 | 통과 | `real-fl/summary.json` |
| 공개 WebSocket → 기존 게이트웨이·제어 스택 + 모의 8개 | 통과 | `gateway-mock/summary.json` |
| 공개 WebSocket → 기존 게이트웨이·제어 스택 + 실제 FL | 통과 | `gateway-real/summary.json` |
| 런치 종료 후 실제 모터 정지 | 확인 | `post-gateway-motor.json` |
| 기존 파일 보존 | 통과 | `production-integrity.json` |

제어 스택 시험에서는 0.02 m/s 직진, 입력 중단에 따른 기존 명령 타임아웃,
후진, 0 명령, 조향 명령, 정상 lifecycle 종료를 확인했다. 네 조향 모의 장치가
기존 제어 계산에 따라 약 0.464 rad를 출력했고 기존 wheel odometry도 수신했다.
실제 FL 정진 joint 속도 평균은 +0.114 rad/s, 후진은 -0.094 rad/s였다.
기대 명령은 0.02 / 0.17 = 약 0.118 rad/s이다. 저전류·저속 단기 동작 시험이며
부하 성능이나 속도 추종 정밀도를 인증하는 시험은 아니다.

공개 WebSocket `wss://10.42.0.1/xbee-ws` 시험은 대시보드와 같은 JSON 메시지를
사용했다. 실제 브라우저의 Gamepad API를 조작한 시험은 아니다.
기존 로컬 XBee PTY 경로로 `/base/cmd_vel`까지 전달됐으며,
실제 FL 정진 평균 +0.108 rad/s, 후진 평균 -0.125 rad/s를 관측했다.
최종 0 명령 이후 기존 CanHW의 deactivate와 shutdown이 로그에 남았다.
종료 시 SIGINT가 프로세스 그룹과 launch에서 전달되어 일부 프로세스는
signal 종료 코드를 기록했지만, CanHW의 deactivate/shutdown 완료와
실제 모터 정지는 별도로 확인했다.

종료 후 읽기 전용 파라미터 조회에서 같은 MCU 식별자
`893e38009023b710`의 `spd_ref=0`, `mechVel=0`, `iqf=0`을 확인했다.
`run_mode=2`는 설정된 속도 제어 모드이며 enable 상태를 의미하지 않는다.
시험 전후 CAN 오류·bus-off·restart 카운터 증가가 없었고,
can0는 500 kbps, ERROR-ACTIVE, TX/RX 오류 카운터 0이었다.
이전 비트레이트 변경 과정의 누적 bus-off/restart 두 건은 그대로 남아 있다.

`real-fl/can-frames.jsonl`에서 송신 대상은 실제 ID 127뿐이었다.
모의 장치 일곱 개에 대한 CAN 송신은 관측되지 않았다.
실기 시험 후 모든 시험 프로세스를 종료했다.

## 확인된 기존 E-Stop 동작

모의 장치만 사용하는 전체 경로에서 E-Stop과 같은 ABORT 메시지를 보냈다.
`/mission_control`에 command 3이 도착했지만 수동 `cmd_drive`를 계속 보내면
기존 주행 컨트롤러의 속도 명령은 0.118 rad/s로 유지됐다.
`gateway-mock/summary.json`에 이 결과가 기록돼 있다.

대시보드 `ControlEstopSection`은 ABORT를 전송하고, 기존 수동 주행 컨트롤러는
`/mission_control`의 ABORT로 수동 명령을 차단하지 않는다.
기존 로버 소프트웨어 보존 요청에 따라 이를 수정하거나 중간에 차단 노드를
추가하지 않았다. 실제 모터에서 주행 입력을 유지하며 ABORT를 시험하지 않았다.

따라서 현재 수동 주행 중 이 버튼을 정지 수단으로 의존하면 안 된다.
트리거를 놓고 스틱을 중립으로 돌려 0 명령을 보내거나, 실행 터미널에서
Ctrl-C로 런치를 종료하는 정지 경로는 실제 모터에서 확인했다.

## 실행 방법

이 장치에서는 빌드가 완료돼 있다. 다른 모터 제어 런치를 종료한 상태에서:

```bash
source /opt/ros/humble/setup.bash
source /home/mr2/mr2-stack/rover/ros2_ws/install/local_setup.bash
source /home/mr2/mr2-stack/rover/flat_bench_ws/install/local_setup.bash
ros2 launch mr2_flat_bench rover_flat.launch.py
```

기본값은 로컬 direct 게이트웨이, 실제 FL 모터 ID 127, can0이다.
기존 dashboard `https://10.42.0.1/`에서 제어를 활성화하고 게임패드를 선택한다.
수동 모드 RT는 전진, LT는 후진이다. 실제 장치는 위의 속도 상한이 적용된다.
CAN 비트레이트는 런치가 변경하지 않으며 현재 모터와 can0 모두 500 kbps이다.

모터를 움직이지 않는 가상 검증은 `real_wheel:=none`으로 실행한다.
카메라 송신을 제외하려면 `enable_video_streaming:=false`, 통신 서비스를
제외하고 기존 제어 스택만 실행하려면 `connection:=none`을 사용한다.
`connection:=base`는 기존 원격 base 게이트웨이 경로를 사용하지만 이번에는
로컬 direct 경로만 검증했다. 일반 로버 launch와 동시에 실행하지 않는다.
