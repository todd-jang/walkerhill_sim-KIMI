"use strict";

const $ = selector => document.querySelector(selector);
const $$ = selector => [...document.querySelectorAll(selector)];
const state = { result: null, tab: "overview", page: 0, filter: "", pricePanel: null, priceLoaded: false };
const PAGE_SIZE = 50;

const METRIC_INFO = {
  paid_booking: ["결제 예약률", "배정 기간 내 성공 결제 예약 1건 이상인 배정자 비율", "percent"],
  net_booking: ["취소 반영 예약률", "기한 내 예약 중 최종 취소 상태가 아닌 배정자 비율", "percent"],
  fully_cancelled: ["완전 취소율", "배정자 중 귀속 예약이 모두 취소된 비율", "percent"],
  booking_count: ["예약 수", "배정 단위당 귀속 예약 수", "count"],
  gross_payment: ["총 결제액", "배정 단위당 서버 확정 결제액", "money"],
  refund_amount: ["완료 환불액", "배정 단위당 cutoff 시점 완료 환불액", "money"],
  net_revenue: ["순매출", "배정 단위당 결제액 − 완료 환불액", "money"],
  variable_cost: ["변동원가", "배정 단위당 연결된 변동원가", "money"],
  contribution_margin: ["기여이익", "배정 단위당 순매출 − 연결된 변동원가", "money"],
  complaint_count: ["불만 건수", "배정자 1,000명당 불만 건수", "events"],
  safety_incident_count: ["안전사고", "배정자 1,000명당 안전사고 건수", "events"],
  survey_responded: ["설문 응답률", "전체 배정자 중 응답한 비율", "percent"],
};

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, char => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[char]);
}
function number(value, digits = 2) {
  return Number(value || 0).toLocaleString("ko-KR", {
    maximumFractionDigits: digits,
    minimumFractionDigits: 0,
  });
}
function money(value, currency = "KRW") {
  return `${number(value, 0)} ${esc(currency)}`;
}
function metricValue(metric, value, currency = "KRW") {
  if (metric === "paid_booking" || metric === "net_booking" ||
      metric === "fully_cancelled" || metric === "survey_responded") {
    return `${number(value * 100, 2)}%`;
  }
  if (["gross_payment", "refund_amount", "net_revenue", "variable_cost",
       "contribution_margin"].includes(metric)) return money(value, currency);
  if (metric === "complaint_count" || metric === "safety_incident_count") {
    return `${number(value * 1000, 2)} / 1,000`;
  }
  return number(value, 3);
}
function effectSummary(metricResult) {
  if (!metricResult?.available) {
    return `<span class="reason">${esc(metricResult?.reason || "결과 없음")}</span>`;
  }
  const metric = metricResult.metric;
  const estimate = metricValue(metric, metricResult.itt_estimate, metricResult.currency);
  const low = metricValue(metric, metricResult.confidence_interval[0], metricResult.currency);
  const high = metricValue(metric, metricResult.confidence_interval[1], metricResult.currency);
  const cls = metricResult.itt_estimate > 0 ? "delta-positive" :
    metricResult.itt_estimate < 0 ? "delta-negative" : "";
  return `<span class="${cls}">${estimate}</span><small>95% CI ${low} – ${high}</small>`;
}
function banner(result) {
  const el = $("#dataBanner");
  if (result.experiment.synthetic_only || result.experiment.mixed_data_origin) {
    el.innerHTML = `<div class="banner synthetic"><b>합성 데이터</b> — 이 결과는 실제 고객 모집단 효과로 해석할 수 없습니다.${result.experiment.mixed_data_origin ? " 합성·실험 출처가 섞여 있습니다. 출처를 분리해 분석을 검증하세요." : ""}</div>`;
  } else {
    el.innerHTML = `<div class="banner real"><b>무작위 실험 원장</b> — 시스템은 배정 기준으로 ITT를 계산합니다. 결과의 타당성은 원장 연결, 누락, 무작위화 수행 및 승인된 데이터 출처를 별도로 확인해야 합니다.</div>`;
  }
}
function renderHeader(exp) {
  $("#experimentHeader").innerHTML = `
    <div><p class="eyebrow">EXPERIMENT RECORD</p><h2>${esc(exp.experiment_id || "실험 ID 미입력")}</h2>
      <div class="meta-list">
        <span>프로토콜 <b>${esc((exp.protocol_id || []).join(", ") || "—")}</b></span>
        <span>분석 기준시각 <b>${esc(exp.analysis_cutoff_at)}</b></span>
        <span>원장 완전성 확인 <b>${esc(exp.source_complete_through)}</b></span>
        <span>귀속 기간 <b>${esc(exp.attribution_window_days)}일 · 배정 시각 기준</b></span>
        <span>데이터 버전 <b>${esc(exp.data_version)}</b></span>
      </div>
    </div><span class="pill">${esc(Object.keys(state.result.segments).length)} SEGMENT${Object.keys(state.result.segments).length === 1 ? "" : "S"}</span>`;
}
function segmentHeading(segment, data) {
  return `<div class="segment-head"><h3>${esc(segment)}</h3><span>${number(data.assignment_count, 0)}명 배정</span></div>`;
}
function kpi(metric, result) {
  const [label, description] = METRIC_INFO[metric];
  if (!result?.available) {
    return `<div class="kpi"><label>${esc(label)}</label><div class="unavailable">${esc(result?.reason || "분석 결과 없음")}</div></div>`;
  }
  const currency = result.currency || "KRW";
  return `<div class="kpi"><label>${esc(label)}</label><strong>${metricValue(metric, result.itt_estimate, currency)}</strong>
    <small>ITT · 처리−대조 · 95% CI ${metricValue(metric, result.confidence_interval[0], currency)} ~ ${metricValue(metric, result.confidence_interval[1], currency)}</small>
    <small>${esc(description)}</small></div>`;
}
function renderOverview(result) {
  const segments = Object.entries(result.segments);
  const keyMetrics = ["paid_booking", "contribution_margin", "net_booking", "complaint_count"];
  $("#tab-overview").innerHTML = `
    <div class="section-title"><div><h2>세그먼트별 주요 ITT</h2><p>처리군으로 배정 − 대조군으로 배정 · 미노출·미구매자 포함</p></div></div>
    <div class="segment-grid">${segments.map(([segment, data]) => `
      <article class="segment-card">${segmentHeading(segment, data)}
        <div class="kpi-grid">${keyMetrics.map(metric => kpi(metric, data.metrics[metric])).join("")}</div>
        <p class="subtle">배정 ${number(data.assigned_per_arm.control, 0)} / ${number(data.assigned_per_arm.treatment, 0)} · 비노출 ${number(data.assignment_count - data.exposed_per_arm.control - data.exposed_per_arm.treatment, 0)}명 · 노출 variant 불일치 ${number(data.exposure_mismatch_count, 0)}건</p>
      </article>`).join("")}</div>
    <div class="section-title"><div><h2>해석 시 유의사항</h2><p>효과 크기와 95% 구간을 같이 확인하세요. 안전사고는 희소할 수 있어 비유의성이 안전의 증거는 아닙니다.</p></div></div>
    <div class="card-row">
      <div class="mini-card"><span>분석 모집단</span><strong>배정 전체</strong><p class="subtle">노출·구매 여부로 필터하지 않음</p></div>
      <div class="mini-card"><span>인과 비교 단위</span><strong>세그먼트별</strong><p class="subtle">가족과 성인 효과를 서로 우열 비교하지 않음</p></div>
      <div class="mini-card"><span>표본·원장 점검</span><strong>${number(result.quality.assignment_count, 0)} 배정</strong><p class="subtle">미귀속 예약 ${number(result.quality.unattributed_booking_count, 0)}건 · 귀속기간 밖 ${number(result.quality.out_of_window_booking_count, 0)}건</p></div>
    </div>`;
}
function renderAssignment(result) {
  const rows = Object.entries(result.segments).map(([segment, data]) => {
    const srm = result.quality.sample_ratio_mismatch_by_segment[segment];
    return `<tr><td>${esc(segment)}</td><td class="number">${number(data.assigned_per_arm.control, 0)}</td>
      <td class="number">${number(data.assigned_per_arm.treatment, 0)}</td>
      <td class="number">${number(data.exposed_per_arm.control, 0)}</td>
      <td class="number">${number(data.exposed_per_arm.treatment, 0)}</td>
      <td class="number">${number(data.assignment_count - data.exposed_per_arm.control - data.exposed_per_arm.treatment, 0)}</td>
      <td class="number">${number(data.exposure_mismatch_count, 0)}</td>
      <td class="number">${number(srm?.p_value_approx, 4)}</td></tr>`;
  }).join("");
  $("#tab-assignment").innerHTML = `
    <div class="section-title"><div><h2>배정과 실제 노출</h2><p>배정 variant는 바뀌지 않습니다. 실제 렌더링 variant가 다르면 오염 진단으로 보존합니다.</p></div></div>
    <div class="table-card table-wrap"><table><thead><tr><th>세그먼트</th><th class="number">대조 배정</th><th class="number">처리 배정</th><th class="number">대조 노출</th><th class="number">처리 노출</th><th class="number">비노출</th><th class="number">variant 불일치</th><th class="number">SRM p 근사</th></tr></thead><tbody>${rows}</tbody></table></div>
    <div class="section-title"><div><h2>배정 및 원장 상태</h2><p>SRM p-value는 1:1을 가정한 근사 진단입니다. 층화·블록 무작위화에 대한 정식 SRM 추론을 대체하지 않습니다.</p></div></div>
    <div class="quality-grid">
      ${qualityCard("배정 단위", result.quality.assignment_count)}
      ${qualityCard("노출 이벤트 불일치", result.quality.exposure_variant_mismatch_count, "실제 표시 variant ≠ 최초 배정 variant", result.quality.exposure_variant_mismatch_count > 0)}
      ${qualityCard("미귀속 예약", result.quality.unattributed_booking_count, "assignment_id 없음 또는 배정표에 없는 ID", result.quality.unattributed_booking_count > 0)}
      ${qualityCard("사전 배정 예약", result.quality.pre_assignment_booking_count, "배정 전에 생성된 예약은 귀속하지 않음", result.quality.pre_assignment_booking_count > 0)}
      ${qualityCard("귀속 기간 밖 예약", result.quality.out_of_window_booking_count, "정해진 배정 기준 귀속 기간 밖 결제", result.quality.out_of_window_booking_count > 0)}
      ${qualityCard("원장 완전성", result.quality.source_complete_through)}
    </div>
    <div class="section-title"><div><h2>배정 프로토콜 버전</h2><p>결과는 기록된 정책·배정·적격성 버전을 따릅니다.</p></div></div>
    ${versionTable(result)}`;
}
function qualityCard(label, value, note = "", alert = false) {
  return `<div class="quality-card ${alert ? "alert" : "ok"}"><div class="q-label">${esc(label)}</div><div class="q-value">${esc(value ?? "—")}</div>${note ? `<div class="q-note">${esc(note)}</div>` : ""}</div>`;
}
function versionTable(result) {
  const rows = Object.entries(result.segments).map(([name, data]) => {
    const fact = state.result.facts.find(item => item.segment === name);
    return `<tr><td>${esc(name)}</td><td>${esc(fact?.protocol_id)}</td><td>${esc(fact?.eligibility_version)}</td><td>${esc(fact?.assignment_logic_version)}</td></tr>`;
  }).join("");
  return `<div class="table-card table-wrap"><table><thead><tr><th>세그먼트</th><th>프로토콜</th><th>적격성 버전</th><th>배정 로직 버전</th></tr></thead><tbody>${rows}</tbody></table></div>`;
}
function financeRow(segment, data, metric) {
  const result = data.metrics[metric];
  if (!result?.available) return `<tr><td>${esc(segment)}</td><td colspan="4" class="reason">${esc(result?.reason || "결과 없음")}</td></tr>`;
  return `<tr><td>${esc(segment)}</td><td class="number">${metricValue(metric, result.control_mean, result.currency)}</td>
    <td class="number">${metricValue(metric, result.treatment_mean, result.currency)}</td>
    <td class="number">${effectSummary(result)}</td><td>${esc(result.interval_method)}</td></tr>`;
}
function renderFinance(result) {
  const metrics = ["paid_booking", "net_booking", "fully_cancelled", "booking_count", "gross_payment", "refund_amount", "net_revenue", "variable_cost", "contribution_margin"];
  const tables = metrics.map(metric => {
    const [label, description] = METRIC_INFO[metric];
    const rows = Object.entries(result.segments).map(([segment, data]) => financeRow(segment, data, metric)).join("");
    return `<div class="section-title"><div><h2>${esc(label)}</h2><p>${esc(description)}</p></div></div>
      <div class="table-card table-wrap"><table><thead><tr><th>세그먼트</th><th class="number">대조군 평균</th><th class="number">처리군 평균</th><th class="number">ITT 효과 · 95% CI</th><th>구간 방법</th></tr></thead><tbody>${rows}</tbody></table></div>`;
  }).join("");
  const reconciliation = Object.entries(result.quality.financial_reconciliation_deltas_by_currency || {}).map(([currency, delta]) =>
    `<tr><td>${esc(currency)}</td><td class="number">${money(delta.gross_payment, currency)}</td><td class="number">${money(delta.refund_amount, currency)}</td><td class="number">${money(delta.variable_cost, currency)}</td></tr>`).join("");
  const cancellationRows = Object.entries(result.segments).map(([segment]) => {
    const segmentFacts = result.facts.filter(fact => fact.segment === segment);
    const armValues = arm => segmentFacts.filter(fact => fact.assigned_variant === arm);
    const fees = arm => {
      const byCurrency = new Map();
      for (const fact of armValues(arm)) {
        byCurrency.set(
          fact.currency,
          (byCurrency.get(fact.currency) || 0) + fact.cancellation_fee_amount,
        );
      }
      return [...byCurrency.entries()].map(([currency, amount]) => money(amount, currency)).join(" · ") || "—";
    };
    return `<tr><td>${esc(segment)}</td>
      <td class="number">${number(armValues("control").reduce((sum, fact) => sum + fact.cancelled_booking_count, 0), 0)}</td>
      <td class="number">${number(armValues("treatment").reduce((sum, fact) => sum + fact.cancelled_booking_count, 0), 0)}</td>
      <td class="number">${fees("control")}</td>
      <td class="number">${fees("treatment")}</td>
      <td class="number">${number(segmentFacts.reduce((sum, fact) => sum + fact.cancellation_event_count, 0), 0)}</td></tr>`;
  }).join("");
  $("#tab-finance").innerHTML = `<div class="section-title"><div><h2>예약 및 재무 결과</h2><p>미구매자는 금액 결과 0으로 배정군에 포함됩니다. 예약 건수 평균은 배정 단위당입니다.</p></div></div>${tables}
    <div class="section-title"><div><h2>취소 원장 및 수수료 참고</h2><p>수수료는 결제액에 이미 포함된 것으로 가정해 매출·기여이익에 더하지 않은 대사용 참고값입니다.</p></div></div>
    <div class="table-card table-wrap"><table><thead><tr><th>세그먼트</th><th class="number">대조 취소 예약</th><th class="number">처리 취소 예약</th><th class="number">대조 수수료 참고</th><th class="number">처리 수수료 참고</th><th class="number">상태 이벤트</th></tr></thead><tbody>${cancellationRows}</tbody></table></div>
    <div class="section-title"><div><h2>원장 대사 차이</h2><p>원장 합계 − 배정 fact 합계. 미귀속·귀속기간 밖 거래가 있으면 차이가 남을 수 있습니다.</p></div></div>
    <div class="table-card table-wrap"><table><thead><tr><th>통화</th><th class="number">결제 차이</th><th class="number">환불 차이</th><th class="number">원가 차이</th></tr></thead><tbody>${reconciliation || `<tr><td colspan="4">대사할 재무 원장이 없습니다.</td></tr>`}</tbody></table></div>
    ${ledgerTotals(result)}`;
}
function ledgerTotals(result) {
  const rows = Object.entries(result.quality.financial_totals_by_currency || {}).map(([currency, totals]) =>
    `<tr><td>${esc(currency)}</td><td class="number">${money(totals.gross_payment, currency)}</td><td class="number">${money(totals.refund_amount, currency)}</td><td class="number">${money(totals.net_revenue, currency)}</td><td class="number">${money(totals.variable_cost, currency)}</td><td class="number">${money(totals.contribution_margin, currency)}</td></tr>`).join("");
  return `<div class="section-title"><div><h2>귀속 원장 총액</h2><p>배정 ID와 귀속 규칙에 연결된 행만 포함한 분석 fact 합계입니다.</p></div></div>
    <div class="table-card table-wrap"><table><thead><tr><th>통화</th><th class="number">총 결제</th><th class="number">환불</th><th class="number">순매출</th><th class="number">변동원가</th><th class="number">기여이익</th></tr></thead><tbody>${rows}</tbody></table></div>`;
}
function renderOperations(result) {
  const sections = Object.entries(result.segments).map(([segment, data]) => {
    const safety = Object.entries(data.safety_incidents_by_severity).map(([severity, count]) => `${esc(severity)} ${number(count, 0)}건`).join(" · ") || "기록된 안전사고 없음";
    return `<article class="segment-card">${segmentHeading(segment, data)}
      <div class="kpi-grid">${kpi("complaint_count", data.metrics.complaint_count)}${kpi("safety_incident_count", data.metrics.safety_incident_count)}</div>
      <div class="section-title"><div><h2>투숙 기준 운영 모니터링</h2><p>사후 투숙자 조건부 비율로, ITT 효과가 아닙니다.</p></div></div>
      <div class="card-row"><div class="mini-card"><span>완료 투숙</span><strong>${number(data.operations_per_stay.completed_stays, 0)}</strong></div>
       <div class="mini-card"><span>불만 / 투숙 100건</span><strong>${data.operations_per_stay.complaints_per_100_completed_stays == null ? "—" : number(data.operations_per_stay.complaints_per_100_completed_stays)}</strong></div>
       <div class="mini-card"><span>사고 / 투숙 100건</span><strong>${data.operations_per_stay.safety_incidents_per_100_completed_stays == null ? "—" : number(data.operations_per_stay.safety_incidents_per_100_completed_stays)}</strong></div></div>
      <p class="subtle">안전사고 중증도: ${safety}. 비유의 결과를 안전의 증거로 해석하지 마세요.</p></article>`;
  }).join("");
  $("#tab-operations").innerHTML = `<div class="section-title"><div><h2>운영·안전 결과</h2><p>배정 단위 ITT와 실제 투숙 기준 모니터링을 분리해 표시합니다.</p></div></div><div class="segment-grid">${sections}</div>`;
}
function renderSurvey(result) {
  const rows = Object.entries(result.segments).map(([segment, data]) => {
    const s = data.survey;
    const response = data.metrics.survey_responded;
    return `<tr><td>${esc(segment)}</td><td class="number">${number(s.assignment_count, 0)}</td><td class="number">${number(s.invited_count, 0)}</td><td class="number">${number(s.responded_count, 0)}</td><td class="number">${number(s.response_rate_per_assignment * 100)}%</td><td class="number">${effectSummary(response)}</td><td class="number">${s.satisfaction_score_respondent_mean == null ? "—" : number(s.satisfaction_score_respondent_mean)}</td><td class="number">${s.recommendation_score_respondent_mean == null ? "—" : number(s.recommendation_score_respondent_mean)}</td></tr>`;
  }).join("");
  $("#tab-survey").innerHTML = `<div class="section-title"><div><h2>설문 결과</h2><p>응답 여부는 전체 배정자 기준 ITT. 만족도·추천 점수는 응답자 한정 기술통계이며 ITT 효과가 아닙니다.</p></div></div>
    <div class="table-card table-wrap"><table><thead><tr><th>세그먼트</th><th class="number">배정</th><th class="number">발송</th><th class="number">응답</th><th class="number">배정 대비 응답률</th><th class="number">응답률 ITT · 95% CI</th><th class="number">응답자 만족도 평균</th><th class="number">응답자 추천 평균</th></tr></thead><tbody>${rows}</tbody></table></div>
    <div class="banner warning">미응답은 0점이나 부정 응답으로 간주하지 않습니다. 응답자 점수의 인과적 비교는 응답 선택 편향 가능성 때문에 주의해서 해석해야 합니다.</div>`;
}
function renderQuality(result) {
  const q = result.quality;
  const cards = [
    ["배정 단위 수", q.assignment_count, "analysis population은 assignment rows"],
    ["노출 variant 불일치", q.exposure_variant_mismatch_count, "assignment와 렌더링 실제값 비교", q.exposure_variant_mismatch_count > 0],
    ["미귀속 예약", q.unattributed_booking_count, "배정 식별자가 없는/등록되지 않은 예약", q.unattributed_booking_count > 0],
    ["사전 배정 예약", q.pre_assignment_booking_count, "배정 전 발생으로 귀속 제외", q.pre_assignment_booking_count > 0],
    ["귀속 기간 밖 예약", q.out_of_window_booking_count, "고정된 배정 시점 기준 기간 외 결제", q.out_of_window_booking_count > 0],
    ["미귀속 결제", q.unattributed_payment_count, "배정 fact에 연결되지 않은 성공 결제", q.unattributed_payment_count > 0],
    ["귀속 전 결제", q.pre_assignment_payment_count, "배정 전 예약에 대한 결제", q.pre_assignment_payment_count > 0],
    ["완료 투숙 미연결", q.unmatched_completed_stay_count, "배정 fact에 연결되지 않은 투숙", q.unmatched_completed_stay_count > 0],
    ["미완료 투숙 기록", q.incomplete_stay_record_count, "cutoff 시점 체크아웃 이전", q.incomplete_stay_record_count > 0],
    ["예약 결과 성숙", q.outcome_mature_count, "귀속 기간이 끝난 배정 단위"],
    ["재무 결과 성숙", q.financially_mature_count, "사전 고정 outcome_matures_at 도달"],
    ["원장 기준시각", q.source_complete_through, "입력자가 확인한 source completeness"],
  ];
  const deltaRows = Object.entries(q.financial_reconciliation_deltas_by_currency || {}).map(([currency, delta]) =>
    `<tr><td>${esc(currency)}</td><td class="number">${money(delta.gross_payment, currency)}</td><td class="number">${money(delta.refund_amount, currency)}</td><td class="number">${money(delta.variable_cost, currency)}</td></tr>`).join("");
  const srmRows = Object.entries(q.sample_ratio_mismatch_by_segment || {}).map(([segment, srm]) =>
    `<tr><td>${esc(segment)}</td><td class="number">${number(srm.observed_treatment, 0)}</td><td class="number">${number(srm.expected_treatment, 2)}</td><td class="number">${number(srm.chi_square_approx, 3)}</td><td class="number">${number(srm.p_value_approx, 4)}</td></tr>`).join("");
  $("#tab-quality").innerHTML = `<div class="section-title"><div><h2>품질·완전성 진단</h2><p>진단은 데이터 완전성 증명이 아닙니다. source_complete_through는 시스템이 원장별로 독립 확인하지 않고 입력자가 확인한 시점입니다.</p></div></div>
    <div class="quality-grid">${cards.map(([label, value, note, alert]) => qualityCard(label, value, note, Boolean(alert))).join("")}</div>
    <div class="section-title"><div><h2>재무 원장 대사</h2><p>원장 합계와 배정 단위 fact 합계 간 차이. 차이가 있으면 원인 분석 후 확정 분석을 진행하세요.</p></div></div>
    <div class="table-card table-wrap"><table><thead><tr><th>통화</th><th class="number">결제 차이</th><th class="number">환불 차이</th><th class="number">원가 차이</th></tr></thead><tbody>${deltaRows || `<tr><td colspan="4">재무 원장이 없습니다.</td></tr>`}</tbody></table></div>
    <div class="section-title"><div><h2>배정비율 이상(SRM) 근사 진단</h2><p>관측 처리 배정 수와 assignment-level 확률 합을 비교합니다. 블록별 층화 randomization을 위한 정식 검정은 아닙니다.</p></div></div>
    <div class="table-card table-wrap"><table><thead><tr><th>세그먼트</th><th class="number">처리 관측</th><th class="number">처리 기대</th><th class="number">χ² 근사</th><th class="number">p 근사</th></tr></thead><tbody>${srmRows}</tbody></table></div>
    <div class="section-title"><div><h2>재현 정보</h2><p>한 번의 결과 재생성에는 고정된 cutoff와 data_version을 사용합니다.</p></div></div>
    ${versionTable(result)}`;
}
function renderFacts(result) {
  const filtered = result.facts.filter(fact => {
    if (!state.filter) return true;
    const haystack = [
      fact.assignment_id, fact.unit_id, fact.segment, fact.assigned_variant,
      fact.first_exposed_at, fact.paid_booking_flag, fact.net_revenue,
      fact.first_exposure_channel, fact.first_exposure_page,
    ].join(" ").toLowerCase();
    return haystack.includes(state.filter.toLowerCase());
  });
  const pages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  state.page = Math.min(state.page, pages - 1);
  const pageRows = filtered.slice(state.page * PAGE_SIZE, (state.page + 1) * PAGE_SIZE);
  const body = pageRows.map(fact => `<tr>
    <td>${esc(fact.assignment_id)}</td><td>${esc(fact.unit_id)}</td><td>${esc(fact.segment)}</td>
    <td>${esc(fact.assigned_variant)}</td><td>${esc(fact.assigned_at)}</td>
    <td>${esc(fact.first_exposed_at || "미노출")}</td><td>${fact.exposed_flag ? `${number(fact.exposure_count, 0)}회` : "아니요"}</td>
    <td>${esc(fact.first_exposed_variant || "—")}</td><td>${esc(fact.first_exposure_page || "—")}</td><td>${esc(fact.first_exposure_channel || "—")}</td>
    <td>${fact.first_exposure_price ? `${money(fact.first_exposure_price.amount, fact.first_exposure_price.currency)}` : "—"}</td>
    <td>${esc(fact.first_exposure_inventory_snapshot ? JSON.stringify(fact.first_exposure_inventory_snapshot) : "—")}</td>
    <td class="number">${fact.paid_booking_flag ? "예" : "아니요"}</td><td class="number">${fact.net_booking_flag ? "예" : "아니요"}</td>
    <td class="number">${number(fact.cancelled_booking_count, 0)} (${number(fact.cancellation_event_count, 0)} events)</td><td class="number">${money(fact.cancellation_fee_amount, fact.currency)}</td>
    <td class="number">${money(fact.net_revenue, fact.currency)}</td><td class="number">${money(fact.contribution_margin, fact.currency)}</td>
    <td class="number">${number(fact.complaint_count, 0)}</td><td class="number">${number(fact.safety_incident_count, 0)}</td>
    <td>${fact.survey_responded_flag ? "응답" : fact.survey_invited_flag ? "미응답" : "미발송"}</td>
  </tr>`).join("");
  $("#tab-facts").innerHTML = `<div class="section-title"><div><h2>배정 단위 최종 fact</h2><p>${number(filtered.length, 0)} / ${number(result.facts.length, 0)}명 · 비노출·미구매자도 같은 테이블에 유지</p></div></div>
    <div class="facts-controls"><input id="factFilter" type="search" value="${esc(state.filter)}" placeholder="배정 ID·세그먼트 검색">
      <div class="pager"><button id="prevPage" ${state.page === 0 ? "disabled" : ""}>이전</button><span>${state.page + 1} / ${pages}</span><button id="nextPage" ${state.page + 1 >= pages ? "disabled" : ""}>다음</button></div></div>
    <div class="table-card table-wrap"><table class="facts-table"><thead><tr><th>assignment_id</th><th>unit_id</th><th>세그먼트</th><th>배정군</th><th>배정 시각</th><th>첫 노출</th><th>노출</th><th>실제 표시군</th><th>페이지</th><th>채널</th><th>노출 가격</th><th>재고 스냅숏</th><th>결제 예약</th><th>취소 반영 예약</th><th class="number">취소</th><th class="number">수수료 참고</th><th class="number">순매출</th><th class="number">기여이익</th><th class="number">불만</th><th class="number">사고</th><th>설문</th></tr></thead><tbody>${body || `<tr><td colspan="21">표시할 배정 단위가 없습니다.</td></tr>`}</tbody></table></div>`;
  $("#factFilter").addEventListener("input", event => {
    state.filter = event.target.value;
    state.page = 0;
    renderFacts(state.result);
    $("#factFilter").focus();
    const input = $("#factFilter");
    input.setSelectionRange(input.value.length, input.value.length);
  });
  $("#prevPage").addEventListener("click", () => { state.page--; renderFacts(state.result); });
  $("#nextPage").addEventListener("click", () => { state.page++; renderFacts(state.result); });
}
function render(result) {
  if (!result) {
    $("#resultWorkspace").classList.add("hidden");
    $("#sessionState").textContent = "대기 중";
    return;
  }
  state.result = result;
  $("#resultWorkspace").classList.remove("hidden");
  $("#sessionState").textContent = "결과 유지 중";
  banner(result);
  renderHeader(result.experiment);
  renderOverview(result);
  renderAssignment(result);
  renderFinance(result);
  renderOperations(result);
  renderSurvey(result);
  renderQuality(result);
  renderFacts(result);
  activateTab(state.tab);
}
function activateTab(name) {
  state.tab = name;
  $$(".tab[data-tab]").forEach(button => button.classList.toggle("active", button.dataset.tab === name));
  $$(".tab-panel").forEach(panel => panel.classList.toggle("hidden", panel.id !== `tab-${name}`));
}
function templateBundle() {
  return {
    experiment: {
      experiment_id: "pilot-2026-v1",
      analysis_cutoff_at: "2026-12-31T23:59:59+09:00",
      source_complete_through: "2026-12-31T23:59:59+09:00",
      attribution_window_days: 7,
      data_version: "source-export-version-1",
    },
    assignments: [{
      experiment_id: "pilot-2026-v1",
      design: "randomized",
      data_origin: "randomized_experiment",
      assignment_id: "approved-pseudo-assignment-001",
      unit_id: "approved-pseudo-unit-001",
      segment: "family",
      assigned_variant: "control",
      assignment_probability: 0.5,
      assigned_at: "2026-12-01T10:00:00+09:00",
      outcome_matures_at: "2026-12-31T23:59:59+09:00",
      randomization_block_id: null,
      protocol_id: "protocol-version",
      eligibility_version: "eligibility-version",
      assignment_logic_version: "assignment-version",
    }],
    exposures: [{
      event_id: "unique-exposure-event-id",
      assignment_id: "approved-pseudo-assignment-001",
      exposed_at: "2026-12-01T10:01:00+09:00",
      rendered_variant: "control",
      page: "family-package-page",
      channel: "web",
      price: {amount: 100000, currency: "KRW"},
      inventory_snapshot: {remaining_rooms: 10},
    }],
    bookings: [{
      booking_id: "pseudonymous-booking-id",
      assignment_id: "approved-pseudo-assignment-001",
      booked_at: "2026-12-02T10:00:00+09:00",
    }],
    payments: [{
      payment_id: "unique-payment-id",
      booking_id: "pseudonymous-booking-id",
      payment_status: "succeeded",
      paid_at: "2026-12-02T10:00:01+09:00",
      amount: 100000,
      currency: "KRW",
    }],
    cancellations: [{
      event_id: "unique-cancellation-event-id",
      booking_id: "pseudonymous-booking-id",
      cancelled_at: "2026-12-03T10:00:00+09:00",
      status: "cancelled",
      fee_amount: 0,
      currency: "KRW",
    }],
    refunds: [{
      event_id: "unique-refund-status-event-id",
      refund_id: "unique-refund-id",
      booking_id: "pseudonymous-booking-id",
      payment_id: "unique-payment-id",
      event_at: "2026-12-04T10:00:00+09:00",
      status: "completed",
      amount: 100000,
      currency: "KRW",
    }],
    costs: [{
      event_id: "unique-cost-event-id",
      booking_id: "pseudonymous-booking-id",
      incurred_at: "2026-12-10T12:00:00+09:00",
      amount: 10000,
      currency: "KRW",
    }],
    stays: [{
      event_id: "unique-stay-event-id",
      booking_id: "pseudonymous-booking-id",
      checked_in_at: "2026-12-20T15:00:00+09:00",
      checked_out_at: "2026-12-21T11:00:00+09:00",
    }],
    operations: [{
      event_id: "unique-operation-event-id",
      assignment_id: "approved-pseudo-assignment-001",
      occurred_at: "2026-12-20T18:00:00+09:00",
      event_type: "complaint",
    }],
    surveys: [{
      survey_id: "unique-survey-id",
      assignment_id: "approved-pseudo-assignment-001",
      invited_at: "2026-12-21T12:00:00+09:00",
      responded_at: "2026-12-21T13:00:00+09:00",
      satisfaction_score: 4,
      recommendation_score: 9,
    }],
  };
}
async function postJson(path, body) {
  const response = await fetch(path, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(body),
  });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
  return payload;
}
function setStatus(message, kind = "") {
  const el = $("#statusMessage");
  el.textContent = message;
  el.className = `status-message ${kind}`;
}

function formatWon(value) {
  return value == null ? "—" : `${number(value, 0)}원`;
}
function formatPct(value) {
  return value == null ? "—" : `${number(value, 2)}%`;
}
function switchView(name) {
  $$(".view-button[data-view]").forEach(button => button.classList.toggle("active", button.dataset.view === name));
  ["experiment", "price", "verdict"].forEach(view => {
    $(`#view-${view}`).classList.toggle("hidden", view !== name);
  });
  if (name === "price" && !state.priceLoaded) loadPricePanel();
}
function priceCell(point) {
  if (!point.parse_ok) {
    return `<span class="badge-muted">미파싸</span><br><span class="raw-cell">${esc(point.raw || "빈 셀")}</span>`;
  }
  const extras = [];
  if (point.weekday != null) extras.push(`주중 ${formatWon(point.weekday)}`);
  if (point.weekend != null) extras.push(`주말 ${formatWon(point.weekend)}`);
  return `<b>${formatWon(point.primary)}</b>${extras.length ? `<br><span class="subtle">${extras.join(" · ")}</span>` : ""}`;
}
function renderPricePanel(data) {
  const source = `${esc(data.source || "—")} · 산출 ${esc(data.generated_at || "—")}`;
  const cards = `<div class="card-row">
      <div class="mini-card"><span>계열 수</span><strong>${number(data.n_series, 0)}</strong><p class="subtle">B_반복 3년 패널</p></div>
      <div class="mini-card"><span>관측 수</span><strong>${number(data.n_obs, 0)}</strong><p class="subtle">파싱 성공 가격 셀</p></div>
      <div class="mini-card"><span>중앙 YoY</span><strong>${formatPct(data.median_yoy_pct)}</strong><p class="subtle">연도별 인상률 중앙값</p></div>
      <div class="mini-card"><span>중앙 주말 프리미엄</span><strong>${formatPct(data.median_weekend_premium_pct)}</strong><p class="subtle">주말가 ÷ 주중가 − 1</p></div>
    </div>`;
  const rows = (data.series || []).map(item => {
    const yearCells = [2024, 2025, 2026].map(year => {
      const point = (item.points || []).find(entry => entry.year === year);
      return `<td class="price-cell">${point ? priceCell(point) : "—"}</td>`;
    }).join("");
    const yoy = (item.yoy_pct || []).map(formatPct).join(" · ") || "—";
    const premium = (item.weekend_premium_pct || []).map(formatPct).join(" · ") || "—";
    const ids = (item.product_ids || []).join(", ") || "—";
    return `<tr><td><b>${esc(item.hotel)}</b><br><span class="subtle">${esc(item.series)}</span></td>
      <td>${esc(ids)}</td>${yearCells}<td class="number">${yoy}</td><td class="number">${premium}</td></tr>`;
  }).join("");
  const notes = (data.notes || []).map(note => `<li>${esc(note)}</li>`).join("");
  $("#priceContent").innerHTML = `${cards}
    <div class="section-title"><div><h2>계열별 3년 가격 패널</h2><p>${source} · 미파싸 셀은 원문을 함께 표시합니다</p></div></div>
    <div class="table-card table-wrap"><table><thead><tr><th>호텔 · 계열</th><th>상품ID</th><th>2024</th><th>2025</th><th>2026</th><th class="number">YoY</th><th class="number">주말 프리미엄</th></tr></thead><tbody>${rows || `<tr><td colspan="7">표시할 계열이 없습니다.</td></tr>`}</tbody></table></div>
    <div class="section-title"><div><h2>산출 규칙</h2><p>해석 전 반드시 확인하세요</p></div></div>
    <ul class="notes-list">${notes || "<li>등록된 산출 규칙이 없습니다.</li>"}</ul>`;
}
async function loadPricePanel() {
  const status = $("#priceStatus");
  status.textContent = "가격 패널 산출물을 불러오는 중…";
  status.className = "status-message";
  try {
    const response = await fetch("/api/price-panel");
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
    state.pricePanel = payload;
    state.priceLoaded = true;
    renderPricePanel(payload);
    status.textContent = "";
  } catch (error) {
    status.textContent = `가격 패널 로드 실패: ${error.message}`;
    status.className = "status-message error";
  }
}
function verdictClass(label) {
  return label === "적합" ? "ok" : label === "부적합" ? "bad" : "conditional";
}
function renderVerdict(report) {
  const metrics = Object.entries(report["보고 지표"] || {}).map(([label, item]) => `<tr>
      <td>${esc(label)}</td><td class="number">${number(item["값"], 4)}</td><td>${esc(item["기준"])}</td>
      <td>${item["통과"] ? `<span class="pass">통과</span>` : `<span class="fail">미달</span>`}</td></tr>`).join("");
  const backtest = report["백테스트"] || {};
  const ci = (backtest["Wilson 95% CI"] || []).map(value => number(value, 4)).join(" – ");
  const engines = Object.entries(report["엔진 통계 라이브러리"] || {})
    .map(([name, available]) => `${name} ${available ? "사용" : "미설치(근사)"}`).join(" · ");
  $("#verdictContent").innerHTML = `
    <div class="verdict-badge ${verdictClass(report["판정"])}"><span>${esc(report["판정"])}</span><small>${esc(report["인용 지침"])}</small></div>
    <div class="section-title"><div><h2>보고 지표</h2><p>판정 기준과의 비교 · 70% 방향·0.60 순위·0.20 기권</p></div></div>
    <div class="table-card"><table><thead><tr><th>지표</th><th class="number">값</th><th>기준</th><th>판정</th></tr></thead><tbody>${metrics}</tbody></table></div>
    <div class="section-title"><div><h2>백테스트</h2><p>주말 방향 적중과 이항 우연 확률</p></div></div>
    <div class="quality-grid">
      <div class="quality-card ok"><div class="q-label">표본 n · 적중 k</div><div class="q-value">${number(backtest["n"], 0)} · ${number(backtest["적중 k"], 0)}</div><div class="q-note">적중률 ${formatPct(backtest["적중률"])}</div></div>
      <div class="quality-card ${backtest["70% 기준이 구간 내"] ? "ok" : "alert"}"><div class="q-label">Wilson 95% CI</div><div class="q-value">${ci || "—"}</div><div class="q-note">70% 기준이 구간 내: ${backtest["70% 기준이 구간 내"] ? "예" : "아니요"}</div></div>
      <div class="quality-card"><div class="q-label">우연 확률 P(X≥k)</div><div class="q-value">${number(backtest["우연 확률 P(X≥k)"], 4)}</div><div class="q-note">n=50 이상에서만 통계적 통과 주장이 가능합니다</div></div>
    </div>
    <p class="subtle">통계 라이브러리: ${esc(engines || "—")}</p>`;
}
async function submitVerdict(event) {
  event.preventDefault();
  const status = $("#verdictStatus");
  const form = new FormData($("#verdictForm"));
  const payload = {
    direction: form.get("direction") === "" ? null : Number(form.get("direction")),
    rho: form.get("rho") === "" ? null : Number(form.get("rho")),
    abstain: form.get("abstain") === "" ? null : Number(form.get("abstain")),
    n_weekends: form.get("n_weekends") === "" ? null : Number(form.get("n_weekends")),
    n_hits: form.get("n_hits") === "" ? null : Number(form.get("n_hits")),
    n_exceed: form.get("n_exceed") === "" ? null : Number(form.get("n_exceed")),
  };
  if (form.get("induction_resist") !== "") payload.induction_resist = Number(form.get("induction_resist"));
  status.textContent = "판정을 계산하는 중…";
  status.className = "status-message";
  try {
    const response = await postJson("/api/verdict", payload);
    renderVerdict(response.result);
    status.textContent = "";
  } catch (error) {
    status.textContent = `판정 계산 실패: ${error.message}`;
    status.className = "status-message error";
    $("#verdictContent").innerHTML = "";
  }
}

$("#fileInput").addEventListener("change", async event => {
  const file = event.target.files?.[0];
  if (!file) return;
  if (file.size > 20 * 1024 * 1024) {
    setStatus("파일은 20MB 이하여야 합니다.", "error");
    return;
  }
  try {
    $("#bundleInput").value = await file.text();
    setStatus(`${file.name} 파일을 읽었습니다. 분석 버튼을 눌러 결과를 생성하세요.`, "success");
  } catch (error) {
    setStatus(`파일 읽기 실패: ${error.message}`, "error");
  }
});
$("#templateButton").addEventListener("click", () => {
  const blob = new Blob([JSON.stringify(templateBundle(), null, 2)], {type: "application/json"});
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = "wise-pilot-ledger-template.json";
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
});
$("#analyzeButton").addEventListener("click", async () => {
  let bundle;
  try {
    bundle = JSON.parse($("#bundleInput").value);
  } catch (error) {
    setStatus(`JSON을 확인해 주세요: ${error.message}`, "error");
    return;
  }
  $("#analyzeButton").disabled = true;
  setStatus("배정 중심 fact 생성과 ITT 계산 중…");
  try {
    const response = await postJson("/api/analyze", bundle);
    render(response.result);
    setStatus("분석 완료. 이 브라우저 세션에 결과를 유지합니다.", "success");
  } catch (error) {
    setStatus(`분석 실패: ${error.message}`, "error");
  } finally {
    $("#analyzeButton").disabled = false;
  }
});
$("#clearButton").addEventListener("click", async () => {
  try {
    await postJson("/api/clear", {});
    state.result = null;
    $("#bundleInput").value = "";
    $("#fileInput").value = "";
    render(null);
    setStatus("현재 세션의 분석 결과를 지웠습니다.");
  } catch (error) {
    setStatus(`세션 초기화 실패: ${error.message}`, "error");
  }
});
$$(".tab[data-tab]").forEach(button => button.addEventListener("click", () => activateTab(button.dataset.tab)));
$$(".view-button[data-view]").forEach(button => button.addEventListener("click", () => switchView(button.dataset.view)));
$("#verdictForm").addEventListener("submit", submitVerdict);

fetch("/api/state")
  .then(response => response.json())
  .then(payload => {
    if (payload.result) {
      render(payload.result);
      setStatus("이 브라우저 세션에 저장된 최근 결과를 불러왔습니다.", "success");
    }
  })
  .catch(() => setStatus("세션을 확인할 수 없습니다. 페이지를 새로고침해 주세요.", "error"));
