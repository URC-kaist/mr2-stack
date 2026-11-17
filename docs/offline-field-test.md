# 오프라인 현장망 테스트

2026-10-29 Rocket 관리 주소 수정: Base/Rover Rocket에 각각
`192.168.1.100/24`, `192.168.1.106/24` 고정 관리 별칭을 추가하고 장비에
영구 저장했습니다. DHCP 대체 주소도 각 장비 주소로 구분했습니다.
900MHz 주 제어는 XBee이며 SiK용 MAVProxy는 기본 비활성화입니다.
실제 신호 조회와 게이트웨이 검증 결과는
[Rocket 실전망 검증 기록](validation/rocket-field-2026-10-29/README.md)을 참고하세요.

2026-09-21 준비. 현재 사용하는 flat 프로필과 USB 카메라 구성 기준입니다.
아래 실행은 설치·다운로드·빌드 없이 이미 준비된 파일을 사용합니다.

## 주소와 연결

| 장치 | 주소 | 인터페이스 |
| --- | --- | --- |
| base | 192.168.1.101/24 | enp2s0 |
| rover | 192.168.1.102/24 | enP8p1s0 |
| 노트북 | 192.168.1.108/24 (다른 장치가 사용 중이면 빈 주소 선택) | 현장망 연결 인터페이스 |
| base Rocket 관리 | 192.168.1.100 | 무선 링크 장치 |

노트북 IPv4를 수동으로 설정합니다. 서브넷 마스크는 `255.255.255.0`입니다.
이 오프라인 테스트에는 기본 게이트웨이와 DNS가 필요 없습니다. `.100`은
Rocket 관리 주소로 기록되어 있으므로 인터넷 라우터 주소로 가정하지 않습니다.
인터넷이 실제로 제공되는 망을 쓰려면 별도로 실제 라우터/DNS 설정이 필요합니다.

base와 rover의 현장용 스크립트는 Ethernet을 고정 IP로 전환합니다.
**각 장치의 로컬 터미널에서 실행하세요. 개발망 주소로 접속한 SSH는 끊길 수 있습니다.**
기존 개발망 NetworkManager 프로필은 보존하며, 현장 프로필은 자동 연결을 켜지 않습니다.
재부팅 후 아래 명령을 실행하면 다시 현장 프로필이 적용됩니다.

## 처음 부팅한 뒤 실행 순서

카메라, XBEE, Ethernet을 연결하고 base를 먼저 켭니다.
전원을 끄지 않고 이번 테스트로 전환한다면 기존 rover ROS 런치와 base gateway를
각 터미널에서 `Ctrl+C`로 먼저 종료하세요. 원격 시간 잠금이 남으면 최대 60초 정도
기다립니다. 실행 중인 런치가 있으면 현장 스크립트는 네트워크를 바꾸지 않고 중단합니다.

### 1. base의 새 터미널

```bash
bash ~/mr2-stack/scripts/start_field.sh base
```

요청되면 해당 장치의 sudo 비밀번호를 입력합니다. 이 터미널은 계속 켜 둡니다.

- 고정 주소 `192.168.1.101/24` 적용.
- nginx의 ROS 연결 대상을 `192.168.1.102:9090`으로 설정, 검증 후 reload.
- 저장된 개발망 설정보다 우선하는 현장 업로드 주소 `http://192.168.1.101` 적용.
- flat 카메라 구성과 레이턴시 측정 활성화.
- SiK가 없으면 이번 실행에서만 MAVProxy 비활성화. XBEE는 그대로 사용.

### 2. rover의 새 터미널

```bash
bash ~/mr2-stack/scripts/start_field.sh rover
```

- 고정 주소 `192.168.1.102/24` 적용.
- **ROS 런치 전에** base 시간에 동기화될 때까지 최대 120초 대기.
- 현장 Ethernet 주소가 활성화된 동안에는 별도 Wi-Fi로 인터넷이 되더라도
  rover는 base만 시간 기준으로 사용. base가 인터넷을 사용할 수 있으면 base를 통해 UTC를 복구.
  현장 주소를 제거하고 개발망으로 복귀하면 rover의 기존 인터넷 시간 서버 설정도 자동 복귀.
- 최근 base 샘플과 시간 오차/불확실성 2ms 이하를 확인한 뒤 flat 프로필로 실행.
- CAN 초기화, 영상 전송, 레이턴시 측정 활성화. 영상 목적지는 `192.168.1.101`.
- ROS 런치가 시작되면 양쪽 UTC 보정 잠금 적용. 상대 시간 동기화는 유지.

이 명령은 실제 flat 하드웨어 런치입니다. 영상 전용 테스트 런치가 아닙니다.
준비 검사에서 실패하면 원인을 고친 뒤 같은 명령을 다시 실행하면 됩니다.
base가 없거나 동기화가 안 되면 이 **측정용 시작 스크립트**는 ROS를 시작하지 않습니다.
일반 `start_chrony.sh`의 base 부재 시 독립 실행 정책은 그대로입니다.

### 3. 노트북

브라우저는 Chrome/Chromium 또는 Edge를 사용하고 다음 주소를 엽니다.

```text
https://192.168.1.101/
```

로컬 자체 서명 인증서 안내가 나오면 이 장치의 주소임을 확인하고 계속 접속합니다.
영상 디코딩에는 HTTPS 보안 컨텍스트가 필요하므로 HTTP로만 사용하지 않습니다.
기존 페이지를 열어두었다면 새로고침합니다.

1. **Latency** 탭에서 `Chrony sync`로 상태 확인.
2. rover와 base의 상대 시간 동기화 준비 완료를 기다림. 런치 직후 몇 초~수십 초 필요할 수 있음.
3. `UTC: Held during ROS launch; UTC accuracy not verified`는 오프라인 실행 중 정상 상태.
   `Policy unavailable` 또는 상대 동기화 실패가 있으면 측정하지 않음.
4. 안정적으로 연결된 **USB 2.2.1만 선택**하고 Uplink 측정.
5. 각 구간의 표본 수가 0보다 큰지 확인. `Jetson frame → RTP`, `H.264 encode`,
   `Base → Browser`, `Decode / Render`, `Jetson frame → Render`를 비교.

Uplink는 게임패드가 필요 없습니다. Downlink는 실제 게임패드를 선택하고
스틱·트리거를 놓아 중립을 확인한 뒤, 입력 안내가 나올 때 조작합니다.
USB 2.2.2는 알려진 물리적 연결 불안정이 있어 첫 비교 측정에서 제외합니다.

## 현장에서 문제를 확인할 명령

base의 **다른 터미널**:

```bash
ip -4 -brief address show enp2s0
ping -c 3 192.168.1.102
curl -ksS https://192.168.1.101/latency/uplink/status | python3 -m json.tool
```

마지막 결과에서 `configured: true`, `capture_interface: enp2s0`,
`public_base_url: http://192.168.1.101`을 확인합니다.

rover의 **다른 터미널**:

```bash
ip -4 -brief address show enP8p1s0
ping -c 3 192.168.1.101
mr2-clock-client status
chronyc -n sources
```

런치 중 `locked`, `base_locked`, `relative_ready`가 모두 `true`,
`base_peer`와 `selected_source`가 `192.168.1.101`이어야 측정할 수 있습니다.
런치 중 `chronyc makestep`, chrony 재설치, 시간 수동 변경은 하지 않습니다.
동기화가 계속 안 되면 런치를 종료한 뒤 연결을 고치고 같은 현장 명령으로 재시작합니다.
`WAIT` 중에는 선택된 시간 서버, 시스템 시간 오차, 현장 base 전용 정책 상태가
10초마다 출력됩니다. `selected=192.168.1.101`이 되어야 합니다.

설치 상태만 검사하려면 (주소 변경·실행 없음):

```bash
# base에서
bash ~/mr2-stack/scripts/start_field.sh base --check
# rover에서
bash ~/mr2-stack/scripts/start_field.sh rover --check
```

## 종료 및 개발망 복귀

rover 런치를 먼저 `Ctrl+C`, 이후 base gateway를 `Ctrl+C`로 종료합니다.
각 장치의 기존 DHCP 프로필로 돌아가는 명령:

```bash
sudo nmcli connection up 'Wired connection 1'
```

base nginx 설정을 바꾸기 전 파일은 `/etc/nginx/sites-available/mr2.before-field-*.bak`에
저장됩니다. 개발망 복귀 시 현재 rover 개발 IP에 맞게 ROS upstream도 복구해야 합니다.
IP가 이전과 같으면 해당 백업을 복구하고 `sudo nginx -t`, `sudo systemctl reload nginx`를 실행합니다.

## 준비 단계에서 검증한 범위

- 두 장치 설치 상태 검사, Node 24 및 ublox/RTCM ROS 메시지 바인딩 검사 통과.
- 현장용 NetworkManager 프로필을 양쪽에 저장하고 고정 IP/자동 연결 해제를 확인.
- 현재 유선 LAN에 현장 IP를 임시로 추가해 양방향 ping, base NTP 응답,
  제한된 SSH 시간 잠금 획득/반납을 검증. NTP 검사는 시계를 바꾸지 않는 `chronyd -Q` 사용.
- 별도 임시 nginx에서 실제 현장 ROS 주소 `.102`로 `/system_status/clock` 수신 성공.
  현장 주소의 대시보드 정적 파일, HTTP/HTTPS 측정 경로 접근 확인.
- 현장 gateway 인수가 개발망 `.env.local` 주소보다 우선함을 실제 base 설정 파서로 확인.
- 기존 런치 중 네트워크 전환을 거부하는 동작 확인. 준비 로직 단위 테스트 통과.

기존 실행은 유지하며 검사했습니다. 전원을 완전히 껐다 켜는 시험,
실제 Rocket 무선 경로의 패킷 손실/지연, 노트북 현장 브라우저 측정은 현장에서 최종 확인합니다.
오프라인에서는 상대 동기화가 가능해도 실제 UTC 날짜의 정확성을 보증하지 않습니다.

### 2026-09-22 현장망 + rover Wi-Fi 동시 연결 수정

rover만 인터넷 NTP에 연결된 상태에서 오프라인 base가 약 103ms 다른 시간을 제공했고,
chrony가 base를 falseticker(`^x`)로 분류해 시작 대기가 끝나지 않는 상황을 확인했습니다.
rover clock policy에 `field_network` 설정을 추가하고 실제 Ethernet 주소에 따라
런치 전 시간 소스를 선택하도록 수정했습니다. 큰 차이는 런치 전에 보정하며,
런치 중의 시간 점프 금지와 base 잠금 확인 정책은 유지합니다.
수정 후 실제 장치에서 base 선택과 시작 대기 조건 통과를 확인했습니다.
하드웨어를 시작하지 않는 45초 잠금 시험에서도 base/rover 잠금 및 측정 준비를 확인했습니다
(확인 시점 시스템 오차 약 0.074ms, chrony 오차 한계 약 1.129ms).
18개 정책/준비 테스트가 양쪽 장치에서 통과했으며, 시험 종료 후 잠금이 해제되었습니다.
