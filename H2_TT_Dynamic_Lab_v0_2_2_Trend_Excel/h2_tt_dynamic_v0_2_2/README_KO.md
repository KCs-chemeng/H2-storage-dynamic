# H₂ TT Dynamic Lab — PLC HMI v0.2.2

## 이번 개정
- 독립 Toplevel **추세 팝업**: 계통 화면을 닫지 않고 여러 추세창 동시 조회.
- 압력·질량유량·표준체적유량·명령/실개도·재고·누적 이송량을 선택.
- 전체 / 최근 10·30·60분 / 사용자 지정 시간범위. 시작·끝 입력 단위는 **분**.
- LIVE 해제는 **추세 화면만 고정**. 시뮬레이션 RUN/PAUSE와 독립.
- `.xlsx` 저장: 요약, 선택추세, 전체기록, 이벤트, 입력조건, 변수설명.
- Excel **편집형 XY 차트**. 가로축은 숫자 시간이며 불규칙 기록간격도 반영.
- 전체 기록은 디스크 저널에서 읽음. 화면용 30,000점 버퍼와 무관.
- EOS 선택은 기존 Leachman / PR / Ideal / 선택 설치 CoolProp 유지.
- 물성·Cv·제어·우선순위·차단 엔진은 v0.2.0과 동일.

## 실행
Python 3.10 이상, Tkinter 포함 환경.

```bat
run_windows.bat
```

```sh
python main.py
# 단일 파일
python H2_TT_Dynamic_v0_2_2.py
```

**Excel 내보내기를 위한 추가 pip 설치는 없음.** Python 표준 라이브러리로 OOXML ZIP을 작성하며 Excel, COM, 매크로 및 온라인 서비스가 필요하지 않음.
CoolProp는 선택 물성모델 사용 시에만 별도 설치.

## 추세 사용
1. 기존처럼 입력조건을 설정하고 AUTO 시작 또는 수동 경로 개방.
2. PROCESS의 **추세 팝업 ↗** 또는 TRENDS 탭의 압력/유량/개도/재고 선택.
3. 왼쪽 신호 목록에서 Ctrl/Shift로 여러 변수 선택. Bank별 SP와 밸브 명령도 선택 가능.
4. 시간범위 선택. 직접 지정 시 **시작/끝 [min]** 입력 후 구간 적용.
5. LIVE 해제: 화면 Snapshot 고정. **새 Snapshot**: 현재 모사상태로 갱신.
6. **표시구간 Excel**: 조회 중인 시간구간을 저장. **전체 Excel**: 해당 Snapshot의 전체 기록 저장.

ESC는 추세창 닫기, 팝업 안의 F5는 Snapshot 갱신, Ctrl+E는 표시구간 Excel 저장.
메인 창 F5는 기존처럼 시간적분 RUN/PAUSE.

명령개도와 실개도를 선택하면 차단명령 이후 전행정 시간에 따른 지연을 비교할 수 있음.
화면은 극값 보존 간소화로 표시하지만 **Excel 원자료에는 간소화를 적용하지 않음**.
차트는 가독성을 위해 선택변수 최초 12개 표시. 선택추세 데이터 표와 전체기록 시트는 모든 변수 저장.

## Excel 파일 구성
| 시트 | 내용 |
|---|---|
| 요약 | EOS, 기록기간·표본수, 최대유량, 이송량, 질량수지 오차, 계산범위 |
| 선택추세 | 선택한 변수의 숫자 데이터 + 편집형 XY 차트 |
| 전체기록 | 압력·유량·재고·명령/실개도·SP·제어권한·모드 등 전체 변수 |
| 이벤트 | 밸브 명령, 모드·SP 변경, 조건 발동·정지 이력 |
| 입력조건 | 초기 설정, 현재 설정, 정상/보호 정지 상세이력 |
| 변수설명 | 변수 키, 단위, 선택항목 표시명 |

- 데이터는 반올림하지 않고 저장. 표시 형식만 소수점 자리 적용.
- time_min은 time_s/60 수식이며 계산 캐시도 함께 저장.
- 메타데이터 내보내기 시각은 UTC, 계산 시간은 s/min.
- 최근 버퍼가 아니라 **현재 실험의 전체 디스크 저널** 사용.
- 시간범위는 기존 기록 표본을 포함/제외하는 필터이며 경계시각을 보간하지 않음.
- 조회 시각이 기록간격 사이면 현재 Snapshot을 마지막 숫자 행으로 추가.
- 10,000행 이상 Excel 차트는 불필요한 중복 캐시를 생략하되 전체 셀 범위를 참조.
- 1,048,500개 표본을 넘으면 구간을 나눠 저장하도록 안내.
- 저장 중에는 캡처한 데이터로 출력 파일만 작성. 시뮬레이션 물리상태는 변경하지 않음.
- 파일이 Excel에서 열려 있으면 덮어쓰기 실패 메시지 표시; 다른 이름으로 저장.
- 프로그램 종료는 진행 중인 Excel 저장이 완료된 뒤 가능.

## EOS: PR과 NRTL
**PR은 이미 구현되어 있음.** `설비 구성 → 압축기 / 물성 → 물성모델 → PR → 새 실험으로 적용`.
EOS 변경 시 같은 P/T/내용적의 초기 질량을 선택한 모델로 다시 계산하므로 시간·재고·이력을 초기화.

- **Leachman**: 기본값. Normal hydrogen 전용 Helmholtz EOS의 압력·밀도 부분.
- **PR**: 내장 표준 Peng–Robinson 기상근. HYSYS 전체 Property Package를 이식한 것은 아님.
- **Ideal**: Z=1 비교모델.
- **CoolProp**: 선택 설치된 HEOS Hydrogen 어댑터. 내장 PR과 다른 선택지.
- **NRTL**: 액상 혼합물의 활동도 모델. 현재 고압 순수 수소 기체의 밀도·압력을 독립적으로 계산하는 EOS가 아니므로 선택항목에 추가하지 않음. 액상혼합물/기액평형 확장에는 별도 기상 EOS와 순수성분 물성 및 상평형 설정을 결합해야 함.

### 코드 계산 비교: 45°C
| 압력 barg | Leachman kg/m³ | PR kg/m³ | PR 차이 |
|---:|---:|---:|---:|
|10|0.834109|0.836690|+0.309%|
|20|1.582573|1.591597|+0.570%|
|190|13.096556|13.550443|+3.466%|
|200|13.707810|14.197151|+3.570%|

이는 두 **내장 구현의 계산 비교**이며 HYSYS 실행 결과 또는 실험 대비 오차표가 아님. 명칭이 PR로 같아도 물성상수·상관식·밀도보정·기상근 등 설정에 따라 결과가 다르므로 동일 P/T에서 비교.
원문 EOS 정확도는 이 프로그램 전체의 정확도 보증을 의미하지 않음. 현재 모델은 계속 등온이며 엔탈피/충전온도/압력파/실제 PID를 계산하지 않음.

### 기술 근거
- NIST Leachman hydrogen EOS: https://www.nist.gov/publications/fundamental-equations-state-parahydrogen-normal-hydrogen-and-orthohydrogen
- CoolProp cubic EOS: https://coolprop.org/coolprop/Cubics.html
- IDAES NRTL 액상 활동도 모델: https://idaes-pse.readthedocs.io/en/2.8.0/_modules/idaes/models/properties/activity_coeff_models/activity_coeff_prop_pack.html

## 예제·검증
- `examples/project_default_PR.json`: 기존 프로젝트 조건의 PR 비교 입력.
- `examples/pressure_rule_stop.json`: 압력조건 STOP과 지연 차단.
- 기존 81개 자동시험(61개 회귀 + 20개 추세/Excel), 독립 추세창 UI 시험 23개.
- `validation/H2_TT_Trend_Export_Demo.xlsx`: 실제 프로그램으로 생성한 PR 예제, 511개 표본.
- `validation/ENGINE_UNCHANGED_v022.json`: 물리/제어 등 6개 소스의 v0.2.1 대비 동일성.
- `validation/EOS_comparison_45C.json`: 위 물성 비교값.
- XML 구조·수식 캐시·숫자 데이터·편집형 차트를 검사했고 별도 스프레드시트 엔진으로 읽기/렌더링 확인.
- Linux / Tk에서 시험. Windows 및 Microsoft Excel 실제 앱에서의 직접 실행 검증은 수행하지 않음.

```sh
python -m unittest discover -s tests -v
python main.py --headless --config examples/project_default_PR.json --auto --hours 0.1 --out results/pr --xlsx results/pr/trends.xlsx
```

## 범위
오프라인 개념검토용, 실제 PLC I/O 없음. 기존 입력과 물리·제어 의미는 `README_ENGINE_v02.md`, `MODEL_NOTES.md` 참조.
