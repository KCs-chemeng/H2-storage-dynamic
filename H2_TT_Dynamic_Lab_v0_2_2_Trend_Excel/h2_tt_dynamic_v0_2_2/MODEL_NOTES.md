# 계산모델과 제어 실행 순서 — v0.2.0

## 1. 출처 및 변경 범위

사용자 제공 H2_TT_Dynamic_Lab_v0_1.zip의 physics.py를 그대로 재사용했습니다. 이 개정은 제어/구성/GUI 개선이며 새로운 공학 성능상관식으로 기존 결과를 대체하지 않았습니다.

기존 프로젝트 입력: TT 22.9 m³, 초기 200 barg, 종료 10 barg, 45°C; Bank A/B 11.7 m³와 C 10.4 m³, 초기 20 barg, 목표 190 barg; 압축기 22.48 kg/h, 흡입 7 barg, 토출상한 200 barg, 참조 75 kW.

개발 예제의 밸브 Cv·행정시간·HH·타이머는 모의실험 가정이며 현장 보호설정값이 아닙니다.

## 2. 재고·물성

```
dm_TT/dt   = -W/3600
dm_Bank/dt = +W/3600
P          = EOS(m/V,T)
```

W kg/h, t s. 같은 dm을 TT에서 빼고 선택 Bank에 더함. 비선택 Bank 질량 불변. 기본 Heun 예측-수정, 밸브 개도는 적분구간 중간값. 각 용기는 등온이며 에너지수지는 풀지 않음.

Leachman normal hydrogen: 잔여 Helmholtz 밀도미분으로 P(ρ,T) 계산, Newton 역산으로 ρ(P,T). 용기 상태는 원식, 밸브의 반복 Z는 0.25 bar 간격 보간. PR 및 Ideal 비교 가능. CoolProp 선택은 별도 설치.

압력단위: 입력/표시 barg, 유동식 bara = barg + 1.01325. Normal 체적은 0°C·1.01325 bara, MW 2.016 kg/kmol, 22.414 Nm³/kmol. Leachman의 고유 분자량 2.01588과 체적환산 분자량의 적용 위치는 v0.1과 동일.

## 3. Cv와 액추에이터

```
x       = (P1-P2)/P1
Fgamma  = k/1.4
x_ch    = Fgamma*xT
x_use   = min(x,x_ch)
Y       = 1 - x_use/(3*x_ch)
W       = 94.8*Fp*Cv_eff*P1*Y*sqrt(MW*x_use/(Z*T1))
```

P1/P2 bara, T1 K, W kg/h. 임계유동을 오류로 취급하지 않음. 이 프로그램은 Cv 입력이며 Kv가 아님.

```
Linear: Cv_eff = Cv_rated*u
EQ-%:   Cv_eff = Cv_rated*(R**u - 1)/(R - 1)
```

u = 실개도 0~1. EQ-%는 0%에서 완전차단되도록 정규화한 프로그램 등가특성. 제조사 실제 Trim 곡선 및 이전 Excel의 R**(u-1)와 구분.

직렬 밸브는 동일 질량유량을 만족하도록 중간압력을 역산/이분법으로 계산. 밸브 사이의 배관 재고·압력파는 없음. 모든 경로는 체크밸브/역류차단을 가정.

ACTUAL 개도는 초당 1/전행정시간만큼 CMD를 추종. Close 시간 미입력 시 개방시간과 같음. 비례 행정모델이며 공압 액추에이터의 실제 지연·마찰 모델은 아님.

## 4. 압축기

```
W_disp = W_rated * speed * rho_suction/rho_rated
```

초기 정격 흡입밀도를 기준으로 유지. 운전 중 흡입 SP를 바꿔도 정격 체적변위 기준은 함께 재정의하지 않음.

MOV/PRV의 공급능력 범위에서 이상적으로 흡입 SP를 유지. 부족하면 공급유량과 변위유량의 교점을 계산. 토출측은 최대압력 경계에서 토출·Bank 밸브의 가용유량으로 제한. 별도 Recycle 경로 없음.

PRV 실개도 표시는 가용 Cv의 상한; 내부의 실제 압력조절 개도/PID를 계산한 것이 아님.

참조 전력량 = ∫ P_reference × speed dt. 제조사 성능맵 또는 전동기 소비전력 예측이 아님.

## 5. 1 step의 처리 순서

1. 해당 시각의 예약 이벤트 적용. 다음 예약시각이 dt 내부에 있으면 간격 분할.
2. 차단완료 확인, 목표/TT종료/HH 확인.
3. 조건식·유지시간·복귀조건 평가.
4. 자동·예약·규칙·수동 출력 수집, 우선순위와 계통 인터록 적용.
5. 액추에이터 중간개도·등가 구동 중간속도로 유량 계산.
6. Heun 방식으로 전달질량 산정, TT/Bank 재고 갱신.
7. 실개도·등가속도 갱신, 시간 진행.
8. 경계조건·규칙 재평가, 종료/절체 요청 기록.
9. 시계열 기록.

조건 평가 시각은 수치시간격에 의해 이산화됨. 압력조건 교차시점을 정확하게 root solve하는 제어기 이벤트해석은 아니므로, 빠른 과도응답은 dt를 줄여 결과 민감도를 확인.

## 6. 지연 차단

IDEAL: TT 종료재고 및 Bank 목표 경계에서 질량 제한, 실제 개도·등가 구동 즉시 0.

DELAYED: 목표·종료값은 **명령발생 조건**이며 질량 경계가 아님. 압축회수 정상정지에서는 현재 유로를 유지하며 구동 감속 후 차단. TRIP에서는 모든 CLOSE 명령을 즉시 적용하되 밸브의 유한 폐쇄시간 및 등가 구동 감속 반영. 그러므로 목표압력 초과 및 TT 종료압력 하회가 계산될 수 있음.

직접 균압의 압력역전 방지를 위한 질량보존 평형경계는 유지. TT 절대재고 음수 방지를 위해 DELAYED의 인출 바닥은 대기압 재고. 설정 HH에서 임의로 질량을 잘라내지 않으며 계산상한 초과는 명시적 오류.

stop_history의 추가유입량 = 차단요청 당시 TT 질량 − 완료 당시 TT 질량. 압력 변화는 동일 구간의 Bank 압력으로 기록.

밸브 사이 배관 재고, 압축기 deadhead 축적, 블로다운·PSV 방출은 이 모델에 없음. 실물 비상차단 검증용으로 사용하지 않음.

## 7. 규칙·권한

조건표의 변수/연산자/숫자만 평가하며 eval/exec 없음. 정상 출력 규칙도 안전 관련 현장 기능을 자동 생성하지 않음.

WHILE는 조건 또는 복귀식까지 유지, LATCH는 Reset/복귀까지 유지, ONCE는 한번 발동 후 재무장 전 추가 발동 없음. 조건 hold는 연속 성립시간이며 false가 되면 0으로 재시작.

같은 우선순위의 밸브출력 충돌은 작은 개도, 압축기 출력은 STOP 우선. 공통 STOP/TRIP은 항상 전체 정지 절차로 처리. MANUAL에서는 경로/Bank/NEXT 규칙 동작을 거부하여 운전자 선택 보존.

보호·인터록 활성 규칙은 원인 해소/Reset 전에 삭제·수정할 수 없음. 종료 래치는 하위 OPEN으로 해제되지 않음.

## 8. 시험과 알려진 범위

61개 자동시험: EOS/유량/질량수지, 개폐, 이전 IDEAL 호환, 임의 Bank 수, 지연 ESD/정상종료, 목표 초과, 실시간 SP, AND/OR, hold, hysteresis, ONCE/LATCH, 우선순위, AUTO 유지 예약, 임의 코드 거부, 파일 출력.

5-Bank 전체운전 예제는 dt=2 s/기록 10 s로 별도 실행한 결과를 포함하며, 기본 프로젝트 전체운전은 dt=0.5 s/기록 2 s. 동일 비교에는 같은 dt를 사용해야 함. 이 테스트는 소프트웨어 내부 검산이며 현장검증이 아님.

실행환경의 Python/Tk 버전은 validation/environment.json에 기록. Windows 배치 스크립트는 제공하되 Windows에서 직접 실행한 것은 아님. CoolProp 비교는 이번 실행환경에 설치되지 않은 경우 검사에서 제외.

## 9. 근거자료

- 원본: H2_TT_Dynamic_Lab_v0_1.zip 및 MODEL_NOTES.md.
- Leachman et al. 2009: https://www.nist.gov/publications/fundamental-equations-state-parahydrogen-normal-hydrogen-and-orthohydrogen
- CoolProp Hydrogen 계수, MIT: https://raw.githubusercontent.com/CoolProp/CoolProp/master/dev/fluids/Hydrogen.json
- Emerson, Valve Sizing Standardized Method: https://www.documentation.emersonprocess.com/intradoc-cgi/groups/public/documents/reference/d351798x012_11.pdf
- Python Tkinter: https://docs.python.org/3.10/library/tkinter.html
- Python unittest: https://docs.python.org/3.10/library/unittest.html

제3자 수치계수 출처와 라이선스는 THIRD_PARTY_NOTICES.txt에 포함.


## v0.2.2 출력·EOS 적용 기록

이번 개정은 물성식·유량식·제어 및 적분 엔진을 변경하지 않는다.
`trend_data.py`는 전체 저널/현재 Snapshot 읽기, `trend_popup.py`는 독립 시각화,
`xlsx_export.py`는 숫자·수식 캐시와 편집형 차트의 OOXML 직렬화만 담당한다.
내보내기 도중 record(force=True)를 호출하지 않으며 물리상태·표본 수를 변경하지 않는다.
화면의 표시 정지와 시간적분 PAUSE는 구분한다.
PR은 기존 표준 alpha(T)와 최대 실근을 사용하고 Leachman을 기본값으로 유지한다.
NRTL은 액상 활동도 모델이므로 현재 등온·순수 수소 기체 EOS 선택 목록에 추가하지 않았다.
추가 설명 및 수치 비교는 README_KO.md와 validation/EOS_comparison_45C.json 참조.
