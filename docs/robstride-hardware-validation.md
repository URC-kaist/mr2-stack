# RS-06 단일 모터 실기 검증

검증 시각: 2026-11-12 23:38 KST. 결과: **통과**.

이 문서는 최초 1 Mbps 시험 기록이다. 이후
[500 kbps 변경 및 재시험](robstride-500k-validation.md)도 통과했고,
현재 can0와 모터는 500 kbps를 사용한다.

flat 프로파일 상태에서 실제 연결된 RS-06 한 대를 시험했다. 실행 중인
flat/ros2_control 모터 제어 프로세스는 없었으며, 4륜 rover launch는 실행하지
않았다. 설치된 `RobstrideDevice`를 pluginlib으로 로드해 기존 CanBusManager와
SocketCAN 경로로 명령·피드백을 송수신했다.

## 연결과 시험 조건

| 항목 | 값 |
| --- | --- |
| CAN 인터페이스 | can0, 1,000,000 bit/s |
| 모터 ID / host ID | 127 / 253 |
| 시험 전 전압 / 속도 | 24.22 V / 0 rad/s |
| 시험 중 전류 제한 | 1 A |
| 시험 중 가속도 설정 | 0.5 rad/s² |
| 목표 속도 및 시간 | +0.2 rad/s 1초, −0.2 rad/s 1초 |
| 각 구간 사이 | zero 명령 0.6초 |
| 피드백 timeout / 모터 watchdog | 각 200 ms |

사용자가 can0를 1 Mbps로 활성화한 뒤 Type 0 identity 조회로 ID 127 한 대를
찾았고 Type 17 파라미터 읽기로 통신과 정지 속도를 확인했다. 모터 ID,
비트레이트 및 flash 설정은 변경하지 않았다.

## 실측 결과

| 항목 | 결과 |
| --- | --- |
| 정회전 구간 마지막 약 0.25초 평균 속도 | +0.2016 rad/s |
| 역회전 구간 마지막 약 0.25초 평균 속도 | −0.2149 rad/s |
| 정회전 / 역회전 위치 변화 | +0.1485 / −0.1316 rad |
| 마지막 비활성화 확인 | Type 2 mode=0, 새 정지 피드백 3개 |
| 비활성화 피드백 평균 속도 | +0.0303 rad/s |
| 종료 후 Type 17 실측 속도 / 전류 | 0 rad/s / 0 A |
| 종료 후 목표 속도 | 0 rad/s |
| 고장 코드가 있는 Type 2 피드백 | 0 / 392 프레임 |
| 피드백 최고 온도 | 22°C |
| CAN bus error / bus-off / 재시작 카운터 | 모두 0 |

순간 속도 피드백에는 정지 중에도 잡음이 관측돼, 속도 추종은 구간 후반의
평균으로 판정했다. 정지는 zero 구간의 속도와 여러 개의 새 mode=0 피드백을
함께 확인하고, 종료 후 파라미터 읽기로 속도·전류가 0인 것을 재확인했다.
run_mode=2 파라미터는 속도 제어 모드 선택값이며, enable 상태와는 별개다.
시험 종료 시 모터는 비활성화돼 있다.

처음 실행한 시험에서는 정·역회전은 수행됐으나 시험 도구의 커널 수신 필터에
CAN_ERR_FLAG를 포함해 독립 모니터가 데이터 프레임을 받지 못했고, 마지막
정지 판정이 실패했다. 별도 SocketCAN 조회로 mode=0, fault=0을 확인했다.
모니터 필터를 수정하고, 움직이기 전 독립 모니터 수신 확인과 여러 정지 샘플
확인을 추가한 뒤 시험 전체를 다시 실행해 통과했다. 모터 플러그인의 프로토콜
구현은 이 실기 시험에서 변경하지 않았다. 관련 필터 구분은
[Linux SocketCAN 문서](https://docs.kernel.org/networking/can.html#raw-socket-option-can-raw-filter)에 설명돼 있다.

## 종료 상태와 범위

- flat 프로파일을 유지했으며 다른 rover 서비스를 시작하지 않았다.
- can0는 사용자가 설정한 1 Mbps 상태로 유지했다.
- 모터에는 zero 명령 및 Type 4 stop을 반복 전송한 뒤 시험 프로세스를 종료했다.
- 시험용 volatile 설정은 run_mode=2, limit_cur=1 A, acc_rad=0.5,
  canTimeout=4000(200 ms)로 남아 있다. flash 저장은 하지 않았다.
- 이번 결과는 이 RS-06 한 대의 저속 정·역회전, 속도·위치 피드백, 기동·정지를
  검증한다. 4륜 통합 주행, 장착 방향, 부하 성능, 전체 각도 순환, 의도적인
  통신 차단·고장 시험까지 검증한 결과는 아니다.

CSV, 원시 CAN 프레임, 전후 파라미터, 첫 시험 실패 기록과 실행 파일 해시는
[`validation/robstride-hardware-2026-11-12`](validation/robstride-hardware-2026-11-12)에 보관했다.

실행 명령:

```bash
cd /home/mr2/mr2-stack/rover/ros2_ws
source /opt/ros/humble/setup.bash
source install/local_setup.bash
ros2 run mr2_devices_robstride robstride_smoke_test --run can0 127 /tmp/mr2-rs06-hardware/motion.csv
```
