const $ = (s) => document.querySelector(s),
  token = $('meta[name="bridge-token"]').content;
let notes = [],
  current = null,
  page = null,
  selected = new Set(),
  profileList = [],
  drag = null;
const urls = new Map();
let noteEpoch = 0,
  pageEpoch = 0,
  replayTimer = null;
function clearUrls(prefix) {
  for (const [key, url] of urls)
    if (key.startsWith(prefix)) {
      URL.revokeObjectURL(url);
      urls.delete(key);
    }
}
function blobUrl(key, blob) {
  clearUrls(key);
  const url = URL.createObjectURL(blob);
  urls.set(key, url);
  return url;
}
function stopReplay() {
  clearInterval(replayTimer);
  replayTimer = null;
  document
    .querySelectorAll("#viewer polyline")
    .forEach((p) => (p.style.visibility = "visible"));
}
function warningsText(warnings) {
  return warnings
    .map((w) => {
      if (w.startsWith("Reverse-engineered parser"))
        return "손글씨 획 중심의 미리보기입니다. 모든 배경·도형·펜 효과가 원본과 같지는 않습니다. 원본과 필압·시간 데이터는 보관됩니다.";
      if (w.startsWith("Added Samsung Notes"))
        return "삼성노트 호환을 위해 마지막에 빈 페이지가 추가됩니다.";
      if (w.startsWith("Generated pressure/timing"))
        return "생성된 필압·시간은 원래 필기 기록과 다릅니다. 삼성노트 앱에서 가져오기·편집을 확인해 주세요.";
      if (w.startsWith("Original PDF content"))
        return "원래 PDF 내용은 그대로 두고, 손글씨를 편집 가능한 잉크 주석으로 더했습니다.";
      if (w.startsWith("In the SDOCX copy"))
        return "삼성노트 사본에서는 PDF 쪽이 배경 이미지가 됩니다. 손글씨는 독립된 펜 획입니다.";
      if (w.startsWith("New SDOCX"))
        return "새 삼성노트의 필압·시간은 생성한 값입니다. 삼성노트 앱에서 열어 확인해 주세요.";
      if (w.startsWith("Your original file is unchanged"))
        return "원본 파일은 그대로입니다. 사본에 모든 쪽·녹음·첨부가 유지됩니다.";
      if (w.startsWith("Samsung Notes format is reverse engineered"))
        return "삼성노트 형식은 공개되지 않은 형식이라, 사본을 삼성노트 앱에서 열어 한 번 확인해 주세요.";
      if (w.startsWith("SVG exports ink only"))
        return "SVG에는 손글씨 획만 포함됩니다. 배경과 입력된 텍스트는 원본·JSON·PDF를 확인하세요.";
      return w;
    })
    .join("\n");
}
function status(message, error = false) {
  $("#status").textContent = message;
  $("#status").classList.toggle("error", error);
}
async function api(path, options = {}) {
  const r = await fetch("/api/" + path, {
    ...options,
    headers: { "x-bridge-token": token, ...options.headers },
  });
  if (!r.ok) {
    let e;
    const message = await r.text();
    try {
      e = JSON.parse(message);
    } catch {
      e = { detail: message || "요청을 처리하지 못했습니다." };
    }
    throw Error(
      typeof e.detail === "string" ? e.detail : JSON.stringify(e.detail),
    );
  }
  return r;
}
async function json(path, options = {}) {
  return (await api(path, options)).json();
}
function post(path, body) {
  return json(path, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
}
async function action(fn) {
  try {
    await fn();
  } catch (e) {
    status(e.message, true);
  }
}
function el(tag, text, cls) {
  let e = document.createElement(tag);
  if (text !== undefined) e.textContent = text;
  if (cls) e.className = cls;
  return e;
}
document.querySelectorAll("[data-tab]").forEach(
  (b) =>
    (b.onclick = () => {
      document
        .querySelectorAll(".tab")
        .forEach((t) =>
          t.classList.toggle("hidden", t.id !== "tab-" + b.dataset.tab),
        );
      document
        .querySelectorAll("[data-tab]")
        .forEach((x) => x.classList.toggle("active", x === b));
    }),
);
function renderNotes() {
  const box = $("#notes");
  box.replaceChildren();
  notes
    .filter((n) =>
      n.title.toLowerCase().includes($("#search").value.toLowerCase()),
    )
    .forEach((n) => {
      const b = el(
        "button",
        n.title,
        "note" + (current === n.id ? " selected" : ""),
      );
      b.append(
        el(
          "small",
          `${n.pages}쪽 · ${n.strokes.toLocaleString()}획 · 녹음 ${n.audio.length}개`,
        ),
      );
      b.onclick = () => action(() => openNote(n.id));
      box.append(b);
    });
  $("#count").textContent = notes.length;
}
async function refresh() {
  notes = await json("notes");
  profileList = await json("profiles");
  renderNotes();
  const old = $("#profile").value;
  $("#profile").replaceChildren();
  profileList.forEach((p) => {
    const o = el("option", p.id + " · " + p.characters + "종");
    o.value = p.id;
    $("#profile").append(o);
  });
  if (profileList.some((p) => p.id === old)) $("#profile").value = old;
  coverage();
  const tid = $("#target").value;
  $("#target").replaceChildren(new Option("새 빈 노트 (A4)", ""));
  for (const [kind, label] of [
    ["pdf", "PDF 위에 쓰기"],
    ["sdocx", "삼성노트에 이어 쓰기"],
  ]) {
    const group = el("optgroup");
    group.label = label;
    notes
      .filter((n) => n.kind === kind)
      .forEach((n) => group.append(new Option(n.title, n.id)));
    if (group.children.length) $("#target").append(group);
  }
  if ([...$("#target").options].some((o) => o.value === tid))
    $("#target").value = tid;
}
function coverage() {
  const p = profileList.find((p) => p.id === $("#profile").value);
  $("#coverage").textContent = p
    ? `직접 기록된 ${p.characters}종, ${p.glyphs.toLocaleString()}개 변형. 합성 음절은 별도 표시합니다.`
    : "글자 사전 JSON을 가져오거나 보관함에서 글자를 선택해 학습하세요.";
}
$("#profile").onchange = coverage;
$("#search").oninput = renderNotes;
$("#files").onchange = () =>
  action(async () => {
    const files = [...$("#files").files];
    let success = 0;
    const failures = [];
    for (let i = 0; i < files.length; i++) {
      status(`가져오는 중 ${i + 1}/${files.length}: ${files[i].name}`);
      const form = new FormData();
      form.append("file", files[i]);
      try {
        await json(
          "upload?profile_name=" + encodeURIComponent($("#profileName").value),
          { method: "POST", body: form },
        );
        success++;
      } catch (e) {
        failures.push(`${files[i].name}: ${e.message}`);
      }
    }
    await refresh();
    status(
      `${success}개 가져오기 완료` +
        (failures.length
          ? ` · ${failures.length}개 실패: ` + failures.join(" / ")
          : ""),
      failures.length > 0,
    );
    $("#files").value = "";
  });
async function openNote(id) {
  const epoch = ++noteEpoch;
  ++pageEpoch;
  current = id;
  page = null;
  drag = null;
  stopReplay();
  clearUrls("note:");
  selected.clear();
  mark();
  $("#viewer").replaceChildren(el("p", "페이지를 불러오는 중…"));
  const n = await json("notes/" + id);
  if (epoch !== noteEpoch) return;
  $("#noteTitle").textContent = n.title;
  $("#noteMeta").textContent =
    `${n.pages}쪽 · ${n.strokes.toLocaleString()}획 · ${n.points.toLocaleString()}개 좌표`;
  $("#noteWarnings").textContent = warningsText(n.warnings);
  $("#pageSelect").replaceChildren();
  n.page_details.forEach((p) =>
    $("#pageSelect").add(new Option(`${p.index + 1} / ${n.pages}쪽`, p.index)),
  );
  noteAudio = n.audio;
  $("#audio").replaceChildren();
  for (const a of n.audio) $("#audio").append(audioRow(id, a, epoch));
  renderNotes();
  await showPage();
}
$("#pageSelect").onchange = () => action(showPage);
let noteAudio = [];
function clock(seconds) {
  const s = Math.max(0, Math.round(seconds)),
    h = Math.floor(s / 3600),
    m = Math.floor((s % 3600) / 60),
    r = s % 60;
  return (h ? h + ":" : "") + String(m).padStart(2, "0") + ":" + String(r).padStart(2, "0");
}
function seconds(text) {
  return text.split(":").reduce((t, v) => t * 60 + Number(v), 0);
}
function syncInk(audio, t) {
  // Show ink written up to this moment of the recording; fade the rest.
  if (!page || audio.start_ms == null) return;
  const now = audio.start_ms + t * 1000;
  const lines = document.querySelectorAll("#viewer polyline");
  page.strokes.forEach((s, i) => {
    if (!lines[i] || s.created_us == null) return;
    const ms = s.created_us / 1000;
    lines[i].classList.toggle("future", ms > now);
    lines[i].classList.toggle("now", ms <= now && ms > now - 8000);
  });
}
function audioRow(id, a, epoch) {
  const row = el("div", undefined, "audio-row");
  const length = a.length_ms ? " · " + clock(a.length_ms / 1000) : "";
  row.append(
    el("strong", (a.title || a.name) + length),
    el("p", `${(a.bytes / 1024 / 1024).toFixed(1)} MB · 원본 그대로 추출 · ${a.name}`, "hint"),
  );
  const actions = el("div", undefined, "toolbar");
  const play = el("button", "▶ 듣기 · 필기와 함께 보기");
  const trans = el("button", "받아쓰기");
  actions.append(play, trans);
  row.append(actions);
  let player = null;
  const transcriptBox = el("div", undefined, "transcript");
  row.append(transcriptBox);
  const ensurePlayer = async () => {
    if (player) return player;
    const blob = await (await api(`notes/${id}/audio/${a.id}`)).blob();
    if (epoch !== noteEpoch) return null;
    const u = blobUrl("note:audio:" + a.id, blob);
    player = el("audio");
    player.controls = true;
    player.src = u;
    player.ontimeupdate = () => syncInk(a, player.currentTime);
    player.onpause = () => {};
    const link = el("a", "녹음 파일 받기", "download-link");
    link.href = u;
    link.download = a.name;
    actions.after(player, link);
    return player;
  };
  play.onclick = () =>
    action(async () => {
      play.disabled = true;
      try {
        const p = await ensurePlayer();
        if (p) {
          p.play();
          status(
            a.start_ms != null
              ? "재생 위치까지 쓴 필기만 진하게 보입니다. 방금 쓴 획은 강조됩니다."
              : "이 녹음은 필기 시각 정보가 없어 동기화 없이 재생합니다.",
          );
        }
      } finally {
        play.disabled = false;
      }
    });
  const showTranscript = (data) => {
    transcriptBox.replaceChildren();
    for (const seg of data.segments) {
      const line = el("button", undefined, "segment");
      const written = (seg.written_meanwhile || [])
        .map((w) => `${w.page}쪽 ${w.strokes}획`)
        .join(", ");
      line.append(
        el("span", seg.at, "time"),
        el("span", seg.text),
        el("small", written ? "필기: " + written : data.alignment.startsWith("No") ? "" : "이때 쓴 필기 없음"),
      );
      if (!written && !data.alignment.startsWith("No")) line.classList.add("unwritten");
      line.onclick = () =>
        action(async () => {
          const p = await ensurePlayer();
          if (!p) return;
          p.currentTime = seconds(seg.at);
          syncInk(a, p.currentTime);
        });
      transcriptBox.append(line);
    }
    const copy = el("button", "받아쓴 글 복사");
    copy.onclick = () =>
      navigator.clipboard.writeText(data.segments.map((s) => `[${s.at}] ${s.text}`).join("\n"));
    transcriptBox.prepend(
      el("p", `${data.model} 모델 · ${data.segments_total}문장 · 붉은 줄은 그 시각에 쓴 필기가 없는 부분입니다.`, "hint"),
      copy,
    );
  };
  const poll = async () => {
    const data = await json(`notes/${id}/audio/${a.id}/transcript`);
    if (epoch !== noteEpoch) return;
    if (data.state === "done") {
      trans.textContent = "받아쓰기 완료";
      trans.disabled = true;
      showTranscript(data);
      return;
    }
    if (data.state === "failed") {
      trans.disabled = false;
      throw Error("받아쓰기 실패: " + data.error);
    }
    if (["starting", "downloading model", "transcribing"].includes(data.state)) {
      trans.disabled = true;
      trans.textContent =
        data.state === "transcribing"
          ? `받아쓰는 중 ${Math.round(data.progress * 100)}% (${data.position}/${data.length})`
          : data.state === "downloading model"
            ? "음성 인식 모델 준비 중… (처음 한 번)"
            : "시작하는 중…";
      setTimeout(() => action(poll), 4000);
    } else if (data.available === false) {
      trans.disabled = true;
      trans.title = "음성 인식 구성요소(sdocx-bridge[audio])가 설치되어 있지 않습니다.";
    }
  };
  trans.onclick = () =>
    action(async () => {
      trans.disabled = true;
      await post(`notes/${id}/audio/${a.id}/transcribe`, { model: "turbo", language: "ko" });
      status("이 컴퓨터에서 받아쓰는 중입니다. 외부로 보내지 않습니다. 긴 강의는 몇 분 이상 걸립니다.");
      await poll();
    });
  action(poll);
  return row;
}
function mark() {
  document
    .querySelectorAll("#viewer polyline")
    .forEach((p) => p.classList.toggle("selected", selected.has(p.dataset.id)));
  $("#selection").textContent =
    `${selected.size}개 획 선택 · 드래그 영역 안에 완전히 들어온 획을 선택합니다.`;
}
function point(event, svg) {
  const p = svg.createSVGPoint();
  p.x = event.clientX;
  p.y = event.clientY;
  return p.matrixTransform(svg.getScreenCTM().inverse());
}
async function showPage() {
  if (!current) return;
  const epoch = ++pageEpoch,
    id = current,
    index = $("#pageSelect").value || 0;
  page = null;
  drag = null;
  selected.clear();
  mark();
  stopReplay();
  clearUrls("note:page");
  $("#viewer").replaceChildren(el("p", "페이지를 불러오는 중…"));
  const loaded = await json(`notes/${id}/pages/${index}`);
  if (epoch !== pageEpoch || id !== current) return;
  page = loaded;
  $("#typedText").textContent =
    page.text || "입력된 텍스트 없음. 손글씨는 페이지를 보고 읽어야 합니다.";
  const viewer = $("#viewer");
  viewer.replaceChildren();
  if (!page.strokes.length) {
    const blob = await (await api(`notes/${id}/pages/${index}/preview`)).blob();
    if (epoch !== pageEpoch || id !== current) return;
    const u = blobUrl("note:page", blob);
    const img = el("img");
    img.src = u;
    img.alt = "PDF 페이지";
    viewer.append(img);
    mark();
    return;
  }
  const ns = "http://www.w3.org/2000/svg",
    svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", `0 0 ${page.width} ${page.height}`);
  svg.setAttribute("aria-label", "손글씨 획 선택 화면");
  for (const s of page.strokes) {
    const p = document.createElementNS(ns, "polyline");
    p.dataset.id = s.id;
    const pts =
      s.points.length === 1
        ? [s.points[0], [s.points[0][0] + 0.001, s.points[0][1]]]
        : s.points;
    p.setAttribute("points", pts.map((p) => p.join(",")).join(" "));
    p.setAttribute("opacity", s.opacity ?? 1);
    p.setAttribute("fill", "none");
    p.setAttribute(
      "stroke",
      `rgb(${s.color.map((c) => Math.round(c * 255)).join(",")})`,
    );
    p.setAttribute("stroke-width", s.width);
    p.setAttribute("stroke-linecap", "round");
    p.setAttribute("stroke-linejoin", "round");
    svg.append(p);
  }
  const box = document.createElementNS(ns, "rect");
  box.setAttribute("class", "selection-box");
  box.setAttribute("visibility", "hidden");
  svg.append(box);
  svg.onpointermove = (e) => {
    if (!drag) return;
    const end = point(e, svg);
    box.setAttribute("x", Math.min(drag.x, end.x));
    box.setAttribute("y", Math.min(drag.y, end.y));
    box.setAttribute("width", Math.abs(end.x - drag.x));
    box.setAttribute("height", Math.abs(end.y - drag.y));
    box.setAttribute("visibility", "visible");
  };
  svg.onpointercancel = () => {
    drag = null;
    box.setAttribute("visibility", "hidden");
  };
  svg.onpointerdown = (e) => {
    stopReplay();
    drag = point(e, svg);
    svg.setPointerCapture(e.pointerId);
  };
  svg.onpointerup = (e) => {
    const end = point(e, svg);
    if (!drag) return;
    if (Math.hypot(end.x - drag.x, end.y - drag.y) < 4) {
      const hit = document.elementFromPoint(e.clientX, e.clientY);
      const id = hit?.dataset.id;
      if (id) {
        selected.has(id) ? selected.delete(id) : selected.add(id);
      }
    } else {
      const x0 = Math.min(drag.x, end.x),
        x1 = Math.max(drag.x, end.x),
        y0 = Math.min(drag.y, end.y),
        y1 = Math.max(drag.y, end.y);
      for (const s of page.strokes)
        if (
          s.points.every(([x, y]) => x >= x0 && x <= x1 && y >= y0 && y <= y1)
        )
          selected.add(s.id);
    }
    drag = null;
    box.setAttribute("visibility", "hidden");
    if (svg.hasPointerCapture(e.pointerId))
      svg.releasePointerCapture(e.pointerId);
    mark();
  };
  viewer.append(svg);
  mark();
}
$("#clearSelection").onclick = () => {
  selected.clear();
  mark();
};
$("#learn").onclick = () =>
  action(async () => {
    if (!current || !page || !selected.size)
      throw Error("학습할 글자의 획을 먼저 선택하세요.");
    await post("learn", {
      note_id: current,
      page_index: Number($("#pageSelect").value),
      label: $("#glyphLabel").value,
      stroke_ids: [...selected],
      profile: $("#learnProfile").value,
    });
    await refresh();
    status("선택한 글자의 실제 획을 사전에 저장했습니다.");
    selected.clear();
    mark();
  });
async function downloads(result, target) {
  target.replaceChildren();
  for (const name of result.files) {
    const b = el("button", name, "download-link");
    b.onclick = () =>
      action(async () => {
        const blob = await (
            await api(`exports/${result.export_id}/${encodeURIComponent(name)}`)
          ).blob(),
          u = URL.createObjectURL(blob),
          a = el("a");
        a.href = u;
        a.download = name;
        a.click();
        setTimeout(() => URL.revokeObjectURL(u), 30000);
      });
    target.append(b);
  }
  if (result.warnings?.length)
    target.append(el("p", warningsText(result.warnings), "hint"));
}
for (const [id, format] of [
  ["exportSvg", "svg"],
  ["exportPdf", "pdf"],
  ["exportJson", "json"],
  ["exportMd", "markdown"],
])
  $("#" + id).onclick = () =>
    action(async () => {
      if (!current) throw Error("노트를 선택하세요.");
      status("내보내는 중…");
      const r = await json(`notes/${current}/export/${format}`, {
        method: "POST",
      });
      $("#downloads").classList.remove("hidden");
      await downloads(r, $("#downloadLinks"));
      status("내보내기 완료");
    });
let placeNote = null,
  placeEpoch = 0,
  placeBox = null;
function numberOrNull(id) {
  const v = $(id).value.trim();
  return v === "" ? null : Number(v);
}
async function loadTarget() {
  const id = $("#target").value;
  placeNote = null;
  placeBox = null;
  $("#startPage").replaceChildren(new Option("1", "0"));
  if (!id) {
    $("#placer").replaceChildren(
      el("div", "새 A4 노트에 왼쪽 위부터 씁니다.", "empty"),
    );
    $("#placeHint").textContent = "";
    $("#size").placeholder = "자동 (22)";
    return;
  }
  const n = await json("notes/" + id);
  if (id !== $("#target").value) return;
  placeNote = n;
  $("#startPage").replaceChildren();
  n.page_details.forEach((p) =>
    $("#startPage").add(new Option(`${p.index + 1} / ${n.pages}`, p.index)),
  );
  await showPlacer();
}
async function showPlacer() {
  if (!placeNote) return;
  const epoch = ++placeEpoch,
    n = placeNote,
    index = Number($("#startPage").value || 0),
    info = n.page_details[index];
  placeBox = null;
  ["#x", "#y", "#width"].forEach((k) => ($(k).value = ""));
  $("#placeHint").textContent = "쓸 자리를 드래그하세요. 드래그한 폭 안에서 줄이 바뀝니다.";
  let auto = n.kind === "pdf" ? 22 : null;
  if (n.kind === "sdocx" && info.strokes) {
    const layout = await json(`notes/${n.id}/pages/${index}/layout`);
    auto = layout.typical_handwriting_height;
  }
  $("#size").placeholder = auto ? `자동 (${Math.round(auto)})` : "자동";
  const blob = await (await api(`notes/${n.id}/pages/${index}/preview`)).blob();
  if (epoch !== placeEpoch) return;
  const wrap = el("div", undefined, "placer-wrap");
  const img = el("img");
  img.src = blobUrl("place:page", blob);
  img.alt = "쓸 페이지";
  const rect = el("div", undefined, "place-rect hidden");
  wrap.append(img, rect);
  $("#placer").replaceChildren(wrap);
  let start = null;
  const toPage = (e) => {
    const r = img.getBoundingClientRect();
    return [
      Math.max(0, Math.min(info.width, ((e.clientX - r.left) / r.width) * info.width)),
      Math.max(0, Math.min(info.height, ((e.clientY - r.top) / r.height) * info.height)),
    ];
  };
  const draw = (x0, y0, x1, y1) => {
    const k = img.getBoundingClientRect().width / info.width;
    Object.assign(rect.style, {
      left: Math.min(x0, x1) * k + "px",
      top: Math.min(y0, y1) * k + "px",
      width: Math.abs(x1 - x0) * k + "px",
      height: Math.max(Math.abs(y1 - y0), 4) * k + "px",
    });
    rect.classList.remove("hidden");
  };
  wrap.onpointerdown = (e) => {
    e.preventDefault();
    start = toPage(e);
    wrap.setPointerCapture(e.pointerId);
  };
  wrap.onpointermove = (e) => {
    if (start) draw(...start, ...toPage(e));
  };
  wrap.onpointerup = (e) => {
    if (!start) return;
    const end = toPage(e);
    let [x0, y0] = [Math.min(start[0], end[0]), Math.min(start[1], end[1])];
    let w = Math.abs(end[0] - start[0]);
    if (w < info.width * 0.05) w = info.width - x0 - info.width * 0.05; // a click: write to the right margin
    draw(x0, y0, x0 + w, Math.max(y0 + Math.abs(end[1] - start[1]), y0 + 4));
    placeBox = { x: x0, y: y0, width: w };
    $("#x").value = Math.round(x0);
    $("#y").value = Math.round(y0);
    $("#width").value = Math.round(w);
    $("#placeHint").textContent = `x ${Math.round(x0)}, y ${Math.round(y0)}, 너비 ${Math.round(w)} · 다시 드래그하면 바뀝니다.`;
    start = null;
  };
}
$("#target").onchange = () => action(loadTarget);
$("#startPage").onchange = () => action(showPlacer);
$("#write").onclick = () =>
  action(async () => {
    if (!$("#profile").value) throw Error("글자 사전이 필요합니다.");
    if (!$("#text").value.trim()) throw Error("쓸 내용을 입력하세요.");
    $("#write").disabled = true;
    status("실제 획으로 문장을 만들고 있습니다…");
    try {
      const r = await post("write", {
        text: $("#text").value,
        profile: $("#profile").value,
        target_id: $("#target").value || null,
        page_index: Number($("#startPage").value || 0),
        x: numberOrNull("#x"),
        y: numberOrNull("#y"),
        size: numberOrNull("#size"),
        width: numberOrNull("#width"),
        color: $("#color").value || null,
        line_spacing: Number($("#lineSpacing").value) || 2.1,
        seed: Number($("#seed").value),
        synthesize: $("#synth").checked,
      });
      const out = $("#writeResult");
      out.className = "";
      await downloads(r, out);
      out.prepend(
        el(
          "p",
          `${r.pages_written.join(", ")}쪽에 ${r.strokes}개 실제 획 · 글자 높이 ${r.size} · 조합한 한글: ${r.assembled_hangul.join(" ") || "없음"}`,
        ),
      );
      clearUrls("result:");
      const img = el("img");
      img.alt = "완성 파일 미리보기";
      out.append(img);
      const blob = await (
        await api(`exports/${r.export_id}/preview/${r.pages_written[0] - 1}`)
      ).blob();
      if (img.isConnected) img.src = blobUrl("result:page", blob);
      status("손글씨 파일을 만들었습니다. 미리보기를 확인하고 내려받으세요.");
    } finally {
      $("#write").disabled = false;
    }
  });
$("#replay").onclick = () => {
  stopReplay();
  if (!page) return;
  const all = [...document.querySelectorAll("#viewer polyline")];
  // Replay in the order the strokes were written (creation time), when known.
  const order = page.strokes
    .map((s, i) => [s.created_us ?? i, i])
    .sort((p, q) => p[0] - q[0])
    .map((p) => all[p[1]])
    .filter(Boolean);
  const lines = order.length === all.length ? order : all;
  if (lines.length > 20000)
    return status("획이 너무 많아 재생을 생략합니다.", true);
  lines.forEach((p) => (p.style.visibility = "hidden"));
  let i = 0;
  replayTimer = setInterval(() => {
    for (
      let n = 0;
      n < Math.max(1, Math.ceil(lines.length / 250)) && i < lines.length;
      n++, i++
    )
      lines[i].style.visibility = "visible";
    if (i >= lines.length) stopReplay();
  }, 20);
  status("필기한 순서대로 다시 보여 줍니다.");
};
action(async () => {
  await refresh();
  await loadTarget();
});
window.addEventListener("beforeunload", () => clearUrls(""));
