"use strict";

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

const STATUS = {
  ACTIVE: { label: "Active", cls: "active" },
  INACTIVE: { label: "Inactive", cls: "inactive" },
  FAULT: { label: "Fault", cls: "fault" },
  UNKNOWN: { label: "Unknown", cls: "unknown" },
};

const GRANULARITY_DAYS = { hourly: 7, daily: 14, monthly: 365 };
const NUMBER = new Intl.NumberFormat(undefined, { maximumFractionDigits: 1 });
const COMPACT = new Intl.NumberFormat(undefined, {
  notation: "compact",
  maximumFractionDigits: 1,
});
const DATE_TIME = new Intl.DateTimeFormat(undefined, {
  dateStyle: "medium",
  timeStyle: "short",
});

const state = {
  view: "fleet",
  page: 1,
  pageSize: 20,
  q: "",
  status: "",
  zone: "",
  total: 0,
  meters: [],
  fleetToken: 0,
  hierarchy: null,
  hierarchyLoading: false,
  drawerToken: 0,
  drawerFocus: null,
  granularity: "daily",
  ready: null,
};

const els = {
  statusChip: $("#statusChip"),
  statusChipText: $("#statusChipText"),
  refreshAll: $("#refreshAll"),
  searchInput: $("#searchInput"),
  statusFilter: $("#statusFilter"),
  zoneFilter: $("#zoneFilter"),
  pageSize: $("#pageSize"),
  metersBody: $("#metersBody"),
  fleetMeta: $("#fleetMeta"),
  fleetQuality: $("#fleetQuality"),
  fleetCache: $("#fleetCache"),
  fleetFetched: $("#fleetFetched"),
  pageLabel: $("#pageLabel"),
  prevPage: $("#prevPage"),
  nextPage: $("#nextPage"),
  kpiTotal: $("#kpiTotal"),
  kpiZones: $("#kpiZones"),
  kpiActive: $("#kpiActive"),
  kpiInactive: $("#kpiInactive"),
  kpiFault: $("#kpiFault"),
  kpiUnknown: $("#kpiUnknown"),
  kpiFlagged: $("#kpiFlagged"),
  kpiCoords: $("#kpiCoords"),
  hierarchyCache: $("#hierarchyCache"),
  hierarchyFetched: $("#hierarchyFetched"),
  hierarchyTree: $("#hierarchyTree"),
  hqOrphans: $("#hqOrphans"),
  hqCycles: $("#hqCycles"),
  hqDuplicates: $("#hqDuplicates"),
  hqMultiParents: $("#hqMultiParents"),
  readyChip: $("#readyChip"),
  readyChipText: $("#readyChipText"),
  rdStatus: $("#rdStatus"),
  rdPortal: $("#rdPortal"),
  rdSession: $("#rdSession"),
  rdBreaker: $("#rdBreaker"),
  rdEntries: $("#rdEntries"),
  rdInflight: $("#rdInflight"),
  rdDetail: $("#rdDetail"),
  rdVersion: $("#rdVersion"),
  healthCache: $("#healthCache"),
  recheckHealth: $("#recheckHealth"),
  adminForm: $("#adminForm"),
  adminKey: $("#adminKey"),
  adminButton: $("#adminButton"),
  adminResult: $("#adminResult"),
  overlay: $("#overlay"),
  drawer: $("#drawer"),
  drawerBody: $("#drawerBody"),
  drawerTitle: $("#drawerTitle"),
  drawerId: $("#drawerId"),
  drawerClose: $("#drawerClose"),
  toastRegion: $("#toastRegion"),
  footerStamp: $("#footerStamp"),
};

class ApiError extends Error {
  constructor(message, status, body, requestId) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.body = body;
    this.requestId = requestId;
  }
}


function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") el.className = value;
    else if (key === "text") el.textContent = value;
    else if (key.startsWith("on") && typeof value === "function") {
      el.addEventListener(key.slice(2).toLowerCase(), value);
    } else if (key === "dataset") {
      for (const [dataKey, dataValue] of Object.entries(value)) {
        el.dataset[dataKey] = dataValue;
      }
    } else {
      el.setAttribute(key, value === true ? "" : value);
    }
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return el;
}

function svgEl(tag, attrs = {}, text) {
  const el = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [key, value] of Object.entries(attrs)) {
    el.setAttribute(key, value);
  }
  if (text !== undefined) el.textContent = text;
  return el;
}

function fmtNum(value, digits = 1) {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return new Intl.NumberFormat(undefined, {
    maximumFractionDigits: digits,
  }).format(value);
}

function fmtCompact(value) {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return COMPACT.format(value);
}

function fmtDateTime(value) {
  if (!value) return "—";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? String(value) : DATE_TIME.format(parsed);
}

function fmtDuration(seconds) {
  if (!Number.isFinite(seconds)) return "";
  if (seconds < 60) return `${Math.round(seconds)}s old`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m old`;
  return `${Math.round(seconds / 3600)}h old`;
}

function describeError(error) {
  if (error instanceof ApiError && error.body) {
    let detail = error.body.detail ?? error.body.title ?? error.message;
    if (Array.isArray(detail)) {
      detail = detail
        .map((item) => {
          const field = Array.isArray(item.loc) ? item.loc.slice(1).join(".") : "";
          return field ? `${field}: ${item.msg}` : item.msg;
        })
        .join("; ");
    }
    return String(detail);
  }
  return error && error.message ? error.message : "Unexpected error";
}

function toast(message, kind = "error") {
  const el = h("div", { class: `toast toast--${kind}`, role: "status" }, message);
  els.toastRegion.append(el);
  setTimeout(() => {
    el.style.opacity = "0";
    el.style.transform = "translateY(6px)";
    el.style.transition = "opacity .18s ease, transform .18s ease";
    setTimeout(() => el.remove(), 220);
  }, 5200);
}

function updateCacheChip(el, response) {
  if (!el) return;
  const stateName = (response.headers.get("X-Cache") || "").toLowerCase();
  const age = Number(response.headers.get("X-Data-Age"));
  if (!stateName) {
    el.textContent = "cache -";
    el.dataset.state = "";
    return;
  }
  el.dataset.state = stateName;
  const suffix = Number.isFinite(age) ? ` · ${fmtDuration(age)}` : "";
  el.textContent = `${stateName}${suffix}`;
}

async function api(path, options = {}) {
  const headers = { Accept: "application/json", ...(options.headers || {}) };
  let body = options.body;
  if (body && typeof body !== "string") {
    body = JSON.stringify(body);
    headers["Content-Type"] = "application/json";
  }
  const response = await fetch(path, { ...options, body, headers });
  const text = await response.text();
  let data = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = text;
    }
  }
  if (!response.ok) {
    throw new ApiError(
      describeError({ body: data, message: response.statusText }),
      response.status,
      data,
      response.headers.get("X-Request-ID"),
    );
  }
  return { data, response };
}

async function fetchReady() {
  const response = await fetch("/ready", { headers: { Accept: "application/json" } });
  let data = null;
  try {
    data = await response.json();
  } catch {
    data = null;
  }
  return { ok: response.ok, status: response.status, data, response };
}

function statusBadge(status) {
  const meta = STATUS[status] || { label: status, cls: "unknown" };
  return h(
    "span",
    { class: `badge badge--${meta.cls}` },
    h("span", { class: "dot" }),
    meta.label.toUpperCase(),
  );
}

function setStatusChip(kind, text) {
  els.statusChip.className = `status-chip status-chip--${kind}`;
  els.statusChipText.textContent = text;
}

function setReadyChip(kind, text) {
  els.readyChip.className = `status-chip status-chip--${kind}`;
  els.readyChipText.textContent = text;
}

function flagElement(flag) {
  let kind = "";
  if (flag.includes("orphan")) kind = "orphan";
  else if (flag.includes("cycle")) kind = "cycle";
  else if (flag.includes("duplicate")) kind = "duplicate";
  else if (flag === "synthetic") kind = "synthetic";
  return h("span", { class: "flag-tag", "data-kind": kind, title: flag }, flag);
}


function renderSkeleton() {
  els.metersBody.replaceChildren(
    ...Array.from({ length: 7 }, () =>
      h(
        "tr",
        { class: "skeleton-row" },
        h("td", {}, h("span", { class: "skeleton", style: "width: 130px" })),
        h("td", {}, h("span", { class: "skeleton", style: "width: 64px" })),
        h("td", {}, h("span", { class: "skeleton", style: "width: 96px" })),
        h("td", {}, h("span", { class: "skeleton", style: "width: 108px" })),
        h("td", {}, h("span", { class: "skeleton", style: "width: 64px" })),
        h("td", {}, h("span", { class: "skeleton", style: "width: 24px" })),
      ),
    ),
  );
}

function renderFleet(data, response) {
  state.meters = data.items;
  state.total = data.total;

  els.metersBody.replaceChildren();
  if (!data.items.length) {
    els.metersBody.append(
      h(
        "tr",
        {},
        h("td", { colspan: "6" }, h("p", { class: "empty" }, "No meters match these filters.")),
      ),
    );
  } else {
    for (const meter of data.items) {
      const flags = meter.quality_flags || [];
      const reading =
        meter.last_reading === null || meter.last_reading === undefined
          ? "—"
          : `${fmtNum(meter.last_reading)}${meter.last_reading_unit ? ` ${meter.last_reading_unit}` : ""}`;
      const row = h(
        "tr",
        {
          tabindex: "0",
          role: "button",
          "aria-label": `Open meter ${meter.id}`,
        },
        h(
          "td",
          {},
          h(
            "div",
            { class: "meter-cell" },
            h("span", { class: "meter-id" }, meter.id),
            h("span", { class: "meter-name" }, meter.display_name || "Unnamed meter"),
          ),
        ),
        h("td", {}, statusBadge(meter.status)),
        h(
          "td",
          {},
          h(
            "div",
            { class: "zone-cell" },
            h("strong", {}, meter.zone_name || "Unassigned"),
            h("span", {}, meter.zone_id || "no zone"),
          ),
        ),
        h("td", {}, fmtDateTime(meter.last_seen_at)),
        h("td", { class: "num" }, reading),
        h(
          "td",
          { class: "num" },
          h(
            "span",
            {
              class: `flag-count${flags.length ? " flag-count--has" : ""}`,
              title: flags.length ? flags.join(", ") : "No quality flags",
            },
            flags.length ? String(flags.length) : "—",
          ),
        ),
      );
      row.addEventListener("click", () => openDrawer(meter.id));
      row.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          openDrawer(meter.id);
        }
      });
      els.metersBody.append(row);
    }
  }

  const start = data.total === 0 ? 0 : (data.page - 1) * data.page_size + 1;
  const end = Math.min(data.total, data.page * data.page_size);
  const filterBits = [];
  if (state.status) filterBits.push(state.status.toLowerCase());
  if (state.zone) filterBits.push(`zone ${state.zone}`);
  if (state.q) filterBits.push(`"${state.q}"`);
  els.fleetMeta.textContent = `Showing ${start}-${end} of ${data.total} meters${
    filterBits.length ? ` · ${filterBits.join(" · ")}` : ""
  }`;

  els.pageLabel.textContent = `Page ${data.page} of ${Math.max(
    1,
    Math.ceil(data.total / data.page_size),
  )}`;
  els.prevPage.disabled = data.page <= 1;
  els.nextPage.disabled = data.next_page === null;

  els.fleetCache && updateCacheChip(els.fleetCache, response);
  els.fleetFetched.textContent = `fetched ${fmtDateTime(data.source.fetched_at)} · ${data.source.source}`;

  const quality = data.data_quality;
  const chips = [];
  if (quality.flagged_meters) {
    chips.push(h("span", { class: "mini-chip mini-chip--warn" }, `${quality.flagged_meters} flagged`));
  }
  if (quality.duplicate_ids) {
    chips.push(h("span", { class: "mini-chip mini-chip--warn" }, `${quality.duplicate_ids} duplicate ids`));
  }
  if (quality.malformed_rows) {
    chips.push(h("span", { class: "mini-chip mini-chip--warn" }, `${quality.malformed_rows} malformed`));
  }
  if (quality.missing_serial) {
    chips.push(h("span", { class: "mini-chip" }, `${quality.missing_serial} missing serial`));
  }
  if (quality.missing_coordinates) {
    chips.push(h("span", { class: "mini-chip" }, `${quality.missing_coordinates} no coordinates`));
  }
  if (quality.unknown_status) {
    chips.push(h("span", { class: "mini-chip" }, `${quality.unknown_status} unknown status`));
  }
  if (!chips.length) {
    chips.push(h("span", { class: "mini-chip" }, "source clean"));
  }
  els.fleetQuality.replaceChildren(...chips);
}

async function loadFleet() {
  const token = ++state.fleetToken;
  if (!state.meters.length) renderSkeleton();
  const params = new URLSearchParams({
    page: String(state.page),
    page_size: String(state.pageSize),
  });
  if (state.status) params.set("status", state.status);
  if (state.zone) params.set("zone", state.zone);
  if (state.q) params.set("q", state.q);

  try {
    const { data, response } = await api(`/api/v1/meters?${params}`);
    if (token !== state.fleetToken) return;
    renderFleet(data, response);
  } catch (error) {
    if (token !== state.fleetToken) return;
    els.metersBody.replaceChildren(
      h(
        "tr",
        {},
        h(
          "td",
          { colspan: "6" },
          h(
            "p",
            { class: "empty" },
            `Could not load meters: ${describeError(error)}`,
          ),
        ),
      ),
    );
    els.fleetMeta.textContent = "Request failed";
    toast(describeError(error));
  }
}

async function loadStats() {
  try {
    const { data } = await api("/api/v1/stats/summary");
    els.kpiTotal.textContent = fmtNum(data.total_meters, 0);
    els.kpiZones.textContent = `${data.zones} zones · ${data.with_coordinates} with coordinates`;
    els.kpiActive.textContent = fmtNum(data.status_counts.active, 0);
    els.kpiInactive.textContent = `${data.status_counts.inactive} inactive`;
    els.kpiFault.textContent = fmtNum(data.status_counts.fault, 0);
    els.kpiUnknown.textContent = `${data.status_counts.unknown} unknown status`;
    els.kpiFlagged.textContent = fmtNum(data.flagged_meters, 0);
    els.kpiCoords.textContent = `${data.with_coordinates} with coordinates`;
    els.kpiZones.textContent = `${data.zones} zones`;
  } catch (error) {
    toast(`Stats unavailable: ${describeError(error)}`);
  }
}

function populateZones(nodes) {
  const current = state.zone;
  const zones = nodes
    .filter(
      (node) =>
        !node.id.startsWith("__") &&
        node.type !== "network" &&
        node.type !== "unassigned",
    )
    .sort((a, b) => a.name.localeCompare(b.name));

  const options = [h("option", { value: "" }, "All zones")];
  for (const zone of zones) {
    const countText = zone.meter_count ? ` (${zone.meter_count})` : "";
    options.push(h("option", { value: zone.id }, `${zone.name}${countText}`));
  }
  els.zoneFilter.replaceChildren(...options);
  if (current && zones.some((zone) => zone.id === current)) {
    els.zoneFilter.value = current;
  } else if (current) {
    state.zone = "";
  }
}


function closeDrawer() {
  state.drawerToken += 1;
  els.drawer.hidden = true;
  els.overlay.hidden = true;
  document.body.classList.remove("no-scroll");
  if (state.drawerFocus && typeof state.drawerFocus.focus === "function") {
    state.drawerFocus.focus();
  }
  state.drawerFocus = null;
}

async function openDrawer(meterId) {
  state.drawerToken += 1;
  const token = state.drawerToken;
  state.drawerFocus = document.activeElement;
  els.drawer.hidden = false;
  els.overlay.hidden = false;
  document.body.classList.add("no-scroll");
  els.drawerId.textContent = meterId;
  els.drawerTitle.textContent = "Loading...";
  els.drawerBody.replaceChildren(
    h("p", { class: "empty" }, "Loading meter detail and consumption..."),
  );
  els.drawerClose.focus();

  try {
    const { data } = await api(`/api/v1/meters/${encodeURIComponent(meterId)}`);
    if (token !== state.drawerToken) return;
    renderDetail(data, token);
  } catch (error) {
    if (token !== state.drawerToken) return;
    els.drawerBody.replaceChildren(
      h("p", { class: "empty" }, `Could not load ${meterId}: ${describeError(error)}`),
    );
    els.drawerTitle.textContent = meterId;
    toast(describeError(error));
  }
}

function detailItem(label, value) {
  return h(
    "div",
    { class: "detail-item" },
    h("span", {}, label),
    h("strong", {}, value === null || value === undefined || value === "" ? "—" : value),
  );
}

function renderDetail(detail, token) {
  els.drawerId.textContent = detail.id;
  els.drawerTitle.textContent = detail.display_name || detail.id;
  const flags = detail.quality_flags || [];
  const coords =
    detail.latitude !== null && detail.longitude !== null
      ? `${detail.latitude.toFixed(5)}, ${detail.longitude.toFixed(5)}`
      : "Missing";
  const reading =
    detail.last_reading === null || detail.last_reading === undefined
      ? "—"
      : `${fmtNum(detail.last_reading)}${detail.last_reading_unit ? ` ${detail.last_reading_unit}` : ""}`;

  const path =
    detail.hierarchy_path && detail.hierarchy_path.length
      ? h(
          "div",
          { class: "path" },
          ...detail.hierarchy_path.flatMap((part, index) =>
            index === 0
              ? [part]
              : [h("i", {}, "/"), part],
          ),
        )
      : h("p", { class: "muted" }, "No hierarchy path available.");

  const summary = h("div", { class: "summary-grid" });
  const chartBox = h("div", { class: "chart-box" }, h("p", { class: "empty" }, "Loading series..."));
  const consumptionFlags = h("div", { class: "flags" });
  const segmented = h(
    "div",
    { class: "segmented", role: "group", "aria-label": "Consumption granularity" },
    ...Object.keys(GRANULARITY_DAYS).map((key) =>
      h(
        "button",
        {
          type: "button",
          class: key === state.granularity ? "is-active" : "",
          "data-granularity": key,
          onclick: () => {
            if (state.granularity === key) return;
            state.granularity = key;
            for (const button of $$("button", segmented)) {
              button.classList.toggle("is-active", button.dataset.granularity === key);
            }
            loadConsumption(detail.id, token, chartBox, summary, consumptionFlags);
          },
        },
        key.charAt(0).toUpperCase() + key.slice(1),
      ),
    ),
  );

  const body = h(
    "div",
    {},
    h(
      "div",
      { class: "detail-top" },
      statusBadge(detail.status),
      h("span", { class: "mini-chip" }, `source: ${detail.source.source}`),
      h("span", { class: "mini-chip" }, detail.portal_id),
    ),
    h(
      "div",
      { class: "detail-grid" },
      detailItem("Serial", detail.serial_number),
      detailItem("Zone", detail.zone_name || detail.zone_id || "Unassigned"),
      detailItem("Install date", detail.install_date),
      detailItem("Last seen", fmtDateTime(detail.last_seen_at)),
      detailItem("Last reading", reading),
      detailItem("Coordinates", coords),
      detailItem("Address", detail.address),
      detailItem("Meter type", detail.meter_type),
      detailItem("Phase", detail.phase),
      detailItem("Multiplier", detail.multiplier === null ? "—" : fmtNum(detail.multiplier, 3)),
    ),
    h("section", { class: "section" }, h("div", { class: "section-head" }, h("h3", {}, "Hierarchy")), path),
    h(
      "section",
      { class: "section" },
      h("div", { class: "section-head" }, h("h3", {}, "Quality flags")),
      h(
        "div",
        { class: "flags" },
        flags.length
          ? flags.map((flag) => h("span", { class: "flag", title: flag }, flag))
          : h("span", { class: "flag flag--none" }, "No defects detected in this record"),
      ),
    ),
    h(
      "section",
      { class: "section" },
      h(
        "div",
        { class: "section-head" },
        h("h3", {}, "Consumption"),
        segmented,
      ),
      chartBox,
      summary,
      h("div", { class: "section", style: "margin-top: 12px" }, consumptionFlags),
    ),
    h(
      "div",
      { class: "source-line" },
      h("span", {}, `${detail.source.system} · fetched ${fmtDateTime(detail.source.fetched_at)}`),
      h("span", {}, `portal id ${detail.portal_id}`),
    ),
  );

  els.drawerBody.replaceChildren(body);
  els.drawerBody.scrollTop = 0;
  loadConsumption(detail.id, token, chartBox, summary, consumptionFlags);
}

async function loadConsumption(meterId, token, chartBox, summaryGrid, flagsBox) {
  chartBox.replaceChildren(h("p", { class: "empty" }, "Loading series..."));
  summaryGrid.replaceChildren();
  flagsBox.replaceChildren();
  const days = GRANULARITY_DAYS[state.granularity] || 14;
  const to = new Date();
  const from = new Date(to.getTime() - days * 24 * 60 * 60 * 1000);
  const params = new URLSearchParams({
    from: from.toISOString(),
    to: to.toISOString(),
    granularity: state.granularity,
  });

  try {
    const { data } = await api(
      `/api/v1/meters/${encodeURIComponent(meterId)}/consumption?${params}`,
    );
    if (token !== state.drawerToken) return;
    renderChart(chartBox, data);
    renderSummary(summaryGrid, data);
    const flags = data.quality_flags || [];
    flagsBox.replaceChildren(
      ...(flags.length
        ? flags.map((flag) => h("span", { class: "flag", title: flag }, flag))
        : [h("span", { class: "flag flag--none" }, "No series defects in this range")]),
    );
  } catch (error) {
    if (token !== state.drawerToken) return;
    chartBox.replaceChildren(
      h("p", { class: "empty" }, `Consumption unavailable: ${describeError(error)}`),
    );
  }
}

function renderSummary(container, data) {
  const unit = data.unit || "";
  const summary = data.summary || {};
  const items = [
    ["Total", summary.total],
    ["Minimum", summary.minimum],
    ["Maximum", summary.maximum],
    ["Average", summary.average],
    ["Missing", summary.missing_points],
  ];
  container.replaceChildren(
    ...items.map(([label, value]) =>
      h(
        "div",
        {},
        h("span", {}, label),
        h(
          "strong",
          {},
          label === "Missing"
            ? fmtNum(value, 0)
            : `${fmtNum(value)}${unit ? ` ${unit}` : ""}`,
        ),
      ),
    ),
  );
}

function renderChart(container, data) {
  const points = Array.isArray(data.points) ? data.points : [];
  const numeric = points
    .map((point) => point.value)
    .filter((value) => typeof value === "number" && Number.isFinite(value));

  container.replaceChildren();
  if (!points.length || !numeric.length) {
    container.append(h("p", { class: "empty" }, "No numeric points in this range."));
    return;
  }

  const width = 720;
  const height = 240;
  const pad = { left: 54, right: 16, top: 16, bottom: 32 };
  const plotWidth = width - pad.left - pad.right;
  const plotHeight = height - pad.top - pad.bottom;
  const rawMin = Math.min(0, ...numeric);
  const rawMax = Math.max(0, ...numeric);
  const span = rawMax - rawMin || 1;
  const min = rawMin >= 0 ? 0 : rawMin - span * 0.06;
  const max = rawMax + span * 0.08;
  const range = max - min || 1;
  const stepX = points.length > 1 ? plotWidth / (points.length - 1) : 0;
  const xFor = (index) =>
    points.length > 1 ? pad.left + index * stepX : pad.left + plotWidth / 2;
  const yFor = (value) => pad.top + ((max - value) / range) * plotHeight;
  const unit = data.unit || "";

  const svg = svgEl("svg", {
    viewBox: `0 0 ${width} ${height}`,
    role: "img",
    "aria-label": `${data.granularity} consumption chart in ${unit}`,
  });

  for (let i = 0; i <= 4; i += 1) {
    const value = max - (i * range) / 4;
    const y = yFor(value);
    svg.append(
      svgEl("line", {
        x1: pad.left,
        x2: width - pad.right,
        y1: y,
        y2: y,
        class: "chart-grid",
      }),
      svgEl(
        "text",
        {
          x: pad.left - 9,
          y: y + 3.5,
          "text-anchor": "end",
          class: "chart-label",
        },
        fmtCompact(value),
      ),
    );
  }

  const zeroY = yFor(0);
  svg.append(
    svgEl("line", {
      x1: pad.left,
      x2: width - pad.right,
      y1: zeroY,
      y2: zeroY,
      class: "chart-zero",
    }),
  );

  let segment = [];
  const segments = [];
  points.forEach((point, index) => {
    if (typeof point.value === "number" && Number.isFinite(point.value)) {
      segment.push({ point, index });
    } else if (segment.length) {
      segments.push(segment);
      segment = [];
    }
  });
  if (segment.length) segments.push(segment);

  for (const current of segments) {
    if (current.length > 1) {
      const linePath = current
        .map(({ point, index }, position) => {
          const command = position === 0 ? "M" : "L";
          return `${command}${xFor(index).toFixed(1)},${yFor(point.value).toFixed(1)}`;
        })
        .join(" ");
      const areaPath = `${linePath} L${xFor(current[current.length - 1].index).toFixed(
        1,
      )},${zeroY.toFixed(1)} L${xFor(current[0].index).toFixed(1)},${zeroY.toFixed(1)} Z`;
      svg.append(
        svgEl("path", { d: areaPath, class: "chart-area" }),
        svgEl("path", { d: linePath, class: "chart-line" }),
      );
    }
  }

  const showDots = points.length <= 70;
  points.forEach((point, index) => {
    const missing = !(typeof point.value === "number" && Number.isFinite(point.value));
    if (missing) {
      svg.append(
        svgEl("circle", {
          cx: xFor(index),
          cy: zeroY,
          r: 2.4,
          class: "chart-missing",
        }),
      );
      return;
    }
    const circle = svgEl("circle", {
      cx: xFor(index),
      cy: yFor(point.value),
      r: showDots ? 3 : 1.6,
      class: `chart-dot${point.value < 0 ? " chart-dot--negative" : ""}`,
    });
    circle.append(
      svgEl(
        "title",
        {},
        `${fmtDateTime(point.timestamp)} · ${fmtNum(point.value)} ${unit}`,
      ),
    );
    svg.append(circle);
  });

  const labelCount = Math.min(6, points.length);
  const stride = labelCount > 1 ? (points.length - 1) / (labelCount - 1) : 0;
  const seen = new Set();
  for (let i = 0; i < labelCount; i += 1) {
    const index = Math.round(i * stride);
    if (seen.has(index)) continue;
    seen.add(index);
    const point = points[index];
    const date = new Date(point.timestamp);
    let label;
    if (Number.isNaN(date.getTime())) label = point.timestamp;
    else if (data.granularity === "hourly") {
      label = date.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
    } else if (data.granularity === "monthly") {
      label = date.toLocaleDateString(undefined, { month: "short", year: "2-digit" });
    } else {
      label = date.toLocaleDateString(undefined, { month: "short", day: "numeric" });
    }
    svg.append(
      svgEl(
        "text",
        {
          x: xFor(index),
          y: height - 10,
          "text-anchor":
            index === 0 ? "start" : index === points.length - 1 ? "end" : "middle",
          class: "chart-label",
        },
        label,
      ),
    );
  }

  const legend = h(
    "div",
    { class: "chart-legend" },
    h("span", {}, h("i", { class: "legend-line" }), `consumption (${unit})`),
    h("span", {}, h("i", { class: "legend-missing" }), "missing point"),
  );
  container.append(svg, legend);
}


function renderHierarchy(data) {
  state.hierarchy = data;
  const quality = data.data_quality;
  els.hqOrphans.textContent = fmtNum(quality.orphans, 0);
  els.hqCycles.textContent = fmtNum(quality.cycles, 0);
  els.hqDuplicates.textContent = fmtNum(quality.duplicates, 0);
  els.hqMultiParents.textContent = fmtNum(quality.multiple_parents, 0);

  const tree = h("ul", { class: "tree" });
  tree.append(renderTreeNode(data.root, 0));
  els.hierarchyTree.replaceChildren(tree);
  populateZones(data.nodes);
}

function renderTreeNode(node, depth) {
  const children = node.children || [];
  const expanded = depth < 2;
  const li = h("li");
  const childList = h("ul");
  const toggle = children.length
    ? h(
        "button",
        {
          class: "tree-toggle",
          type: "button",
          "aria-expanded": expanded ? "true" : "false",
          "aria-label": `Toggle ${node.name}`,
          onclick: (event) => {
            const next = toggle.getAttribute("aria-expanded") !== "true";
            toggle.setAttribute("aria-expanded", String(next));
            childList.hidden = !next;
            event.stopPropagation();
          },
        },
        "›",
      )
    : h("span", { class: "tree-toggle tree-toggle--spacer", "aria-hidden": "true" }, "›");

  const row = h(
    "div",
    { class: "tree-node" },
    toggle,
    h("span", { class: "tree-name" }, node.name),
    h("span", { class: "tree-id" }, node.id),
    h("span", { class: "tree-type" }, node.type),
    h(
      "div",
      { class: "tree-flags" },
      ...(node.quality_flags || []).map((flag) => flagElement(flag)),
    ),
    h(
      "span",
      { class: "tree-count", title: "Meters in this subtree" },
      `${fmtNum(node.meter_count, 0)} m`,
    ),
  );
  li.append(row);

  if (children.length) {
    childList.hidden = !expanded;
    for (const child of children) childList.append(renderTreeNode(child, depth + 1));
    li.append(childList);
  }
  return li;
}

async function loadHierarchy(options = {}) {
  if (state.hierarchyLoading) return;
  state.hierarchyLoading = true;
  if (!options.quiet) {
    els.hierarchyTree.replaceChildren(h("p", { class: "empty" }, "Loading hierarchy..."));
  }
  try {
    const { data, response } = await api("/api/v1/hierarchy");
    renderHierarchy(data);
    updateCacheChip(els.hierarchyCache, response);
    els.hierarchyFetched.textContent = `fetched ${fmtDateTime(data.source.fetched_at)} · ${data.source.source}`;
  } catch (error) {
    if (!options.quiet) {
      els.hierarchyTree.replaceChildren(
        h("p", { class: "empty" }, `Could not load hierarchy: ${describeError(error)}`),
      );
      toast(describeError(error));
    }
  } finally {
    state.hierarchyLoading = false;
  }
}


async function loadHealthChip() {
  try {
    const ready = await fetchReady();
    state.ready = ready;
    if (ready.ok && ready.data?.status === "ready") {
      setStatusChip("good", "Portal ready");
    } else {
      setStatusChip("bad", ready.data?.status === "degraded" ? "Portal degraded" : "Portal unreachable");
    }
    els.footerStamp.textContent = `checked ${new Date().toLocaleTimeString()}`;
  } catch {
    setStatusChip("bad", "Portal unreachable");
  }
}

async function loadHealthView() {
  setReadyChip("pending", "Checking");
  els.readyChip.className = "status-chip status-chip--pending";
  let health = null;
  let ready = null;
  try {
    const healthResult = await api("/health");
    health = healthResult.data;
  } catch (error) {
    toast(`Health check failed: ${describeError(error)}`);
  }
  try {
    ready = await fetchReady();
    state.ready = ready;
  } catch (error) {
    toast(`Readiness probe failed: ${describeError(error)}`);
  }

  if (ready) {
    const body = ready.data || {};
    els.healthCache.textContent = `probe ${ready.status}`;
    els.healthCache.dataset.state = ready.ok ? "hit" : "stale";
    els.rdStatus.textContent = body.status || (ready.ok ? "ready" : "unavailable");
    els.rdPortal.textContent = body.portal_ok ? "ok" : "unavailable";
    els.rdSession.textContent = body.session_authenticated ? "authenticated" : "not authenticated";
    els.rdBreaker.textContent = body.breaker || "unknown";
    els.rdEntries.textContent = fmtNum(body.cache_entries, 0);
    els.rdInflight.textContent = fmtNum(body.cache_refreshes_inflight, 0);
    els.rdDetail.textContent = body.detail || "—";
    if (ready.ok && body.status === "ready") {
      setReadyChip("good", "Ready");
      setStatusChip("good", "Portal ready");
    } else {
      setReadyChip("bad", body.status === "degraded" ? "Degraded" : "Unavailable");
      setStatusChip("bad", "Portal degraded");
    }
  } else {
    els.healthCache.textContent = "probe failed";
    els.healthCache.dataset.state = "stale";
    setReadyChip("bad", "Unavailable");
    setStatusChip("bad", "Portal unreachable");
  }
  els.rdVersion.textContent = health ? `${health.version} · ${health.environment}` : "—";
}


async function submitAdminRefresh(event) {
  event.preventDefault();
  const key = els.adminKey.value.trim();
  if (!key) {
    toast("Enter the admin API key first.");
    els.adminKey.focus();
    return;
  }
  els.adminButton.disabled = true;
  els.adminButton.textContent = "Refreshing...";
  try {
    const { data } = await api("/api/v1/admin/cache/refresh", {
      method: "POST",
      headers: { "X-API-Key": key },
    });
    els.adminResult.hidden = false;
    els.adminResult.textContent = `invalidated ${data.invalidated_entries} · meters ${data.meters_reloaded} · hierarchy nodes ${data.hierarchy_reloaded} · ${fmtDateTime(data.refreshed_at)}`;
    toast("Caches refreshed.", "success");
    await Promise.allSettled([loadFleet(), loadStats(), loadHierarchy({ quiet: true }), loadHealthView()]);
  } catch (error) {
    els.adminResult.hidden = true;
    toast(describeError(error));
  } finally {
    els.adminButton.disabled = false;
    els.adminButton.textContent = "Refresh caches";
  }
}


function setView(name, { updateHash = true } = {}) {
  const allowed = ["fleet", "hierarchy", "health"];
  const next = allowed.includes(name) ? name : "fleet";
  state.view = next;
  for (const view of $$(".view")) {
    view.hidden = view.id !== `view-${next}`;
  }
  for (const tab of $$(".tab")) {
    tab.classList.toggle("is-active", tab.dataset.view === next);
  }
  if (updateHash && window.location.hash !== `#${next}`) {
    history.replaceState(null, "", `#${next}`);
  }
  if (next === "hierarchy" && !state.hierarchy) loadHierarchy();
  if (next === "health") loadHealthView();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function wireEvents() {
  for (const tab of $$(".tab")) {
    tab.addEventListener("click", () => setView(tab.dataset.view));
  }
  window.addEventListener("hashchange", () => setView(window.location.hash.slice(1)));

  let searchTimer = null;
  els.searchInput.addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      state.q = els.searchInput.value.trim();
      state.page = 1;
      loadFleet();
    }, 280);
  });
  els.statusFilter.addEventListener("change", () => {
    state.status = els.statusFilter.value;
    state.page = 1;
    loadFleet();
  });
  els.zoneFilter.addEventListener("change", () => {
    state.zone = els.zoneFilter.value;
    state.page = 1;
    loadFleet();
  });
  els.pageSize.addEventListener("change", () => {
    state.pageSize = Number(els.pageSize.value) || 20;
    state.page = 1;
    loadFleet();
  });
  els.prevPage.addEventListener("click", () => {
    if (state.page <= 1) return;
    state.page -= 1;
    loadFleet();
  });
  els.nextPage.addEventListener("click", () => {
    state.page += 1;
    loadFleet();
  });

  els.refreshAll.addEventListener("click", refreshAll);
  els.drawerClose.addEventListener("click", closeDrawer);
  els.overlay.addEventListener("click", closeDrawer);
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !els.drawer.hidden) closeDrawer();
  });

  els.adminForm.addEventListener("submit", submitAdminRefresh);
  els.recheckHealth.addEventListener("click", () => loadHealthView());
}

async function refreshAll() {
  els.refreshAll.classList.add("is-spinning");
  els.refreshAll.disabled = true;
  try {
    await Promise.allSettled([
      loadFleet(),
      loadStats(),
      loadHealthChip(),
      loadHierarchy({ quiet: true }),
    ]);
    if (state.view === "health") await loadHealthView();
    toast("Data refreshed.", "success");
  } finally {
    els.refreshAll.classList.remove("is-spinning");
    els.refreshAll.disabled = false;
  }
}

async function init() {
  wireEvents();
  setView((window.location.hash || "#fleet").slice(1), { updateHash: false });
  renderSkeleton();
  await Promise.allSettled([
    loadFleet(),
    loadStats(),
    loadHealthChip(),
    loadHierarchy({ quiet: true }),
  ]);
}

init();
