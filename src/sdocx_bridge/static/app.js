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
        return "PDF 출력은 원래 문서를 유지합니다. SDOCX의 PDF 배경은 이미지로 바뀌지만, 손글씨는 독립된 펜 획입니다.";
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
  const pid = $("#pdf").value;
  $("#pdf").replaceChildren(new Option("새 빈 노트 (A4)", ""));
  notes
    .filter((n) => n.kind === "pdf")
    .forEach((n) => $("#pdf").add(new Option(n.title, n.id)));
  $("#pdf").value = pid;
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
  $("#audio").replaceChildren();
  for (const a of n.audio) {
    const row = el("div", undefined, "audio-row");
    row.append(
      el("strong", a.name),
      el("p", `${(a.bytes / 1024 / 1024).toFixed(1)} MB · 원본 그대로 추출`),
    );
    const b = el("button", "재생 / 다운로드 준비");
    b.onclick = () =>
      action(async () => {
        b.disabled = true;
        let blob;
        try {
          blob = await (await api(`notes/${id}/audio/${a.id}`)).blob();
        } finally {
          b.disabled = false;
        }
        if (epoch !== noteEpoch) return;
        const u = blobUrl("note:audio:" + a.id, blob);
        const player = el("audio");
        player.controls = true;
        player.src = u;
        const link = el("a", "녹음 다운로드", "download-link");
        link.href = u;
        link.download = a.name;
        row.append(player, link);
        b.remove();
      });
    row.append(b);
    $("#audio").append(row);
  }
  renderNotes();
  await showPage();
}
$("#pageSelect").onchange = () => action(showPage);
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
$("#write").onclick = () =>
  action(async () => {
    if (!$("#profile").value) throw Error("글자 사전이 필요합니다.");
    $("#write").disabled = true;
    status("실제 획으로 문장을 만들고 있습니다…");
    try {
      const r = await post("write", {
        text: $("#text").value,
        profile: $("#profile").value,
        pdf_id: $("#pdf").value || null,
        page_index: Number($("#startPage").value) - 1,
        x: Number($("#x").value),
        y: Number($("#y").value),
        size: Number($("#size").value),
        width: Number($("#width").value),
        seed: Number($("#seed").value),
        synthesize: $("#synth").checked,
      });
      const out = $("#writeResult");
      out.className = "";
      await downloads(r, out);
      out.prepend(
        el(
          "p",
          `${r.pages}쪽 / ${r.strokes}개 실제 획 · 조합한 한글: ${r.assembled_hangul.join(" ") || "없음"} · 만든 기호: ${r.derived_symbols.join(" ") || "없음"}`,
        ),
      );
      clearUrls("result:");
      const chooser = el("select");
      chooser.setAttribute("aria-label", "완성 파일 미리보기 쪽");
      for (let i = 0; i < r.pages; i++)
        chooser.add(new Option(`${i + 1} / ${r.pages}쪽`, i));
      chooser.value = String(
        Math.min(r.pages - 1, Math.max(0, Number($("#startPage").value) - 1)),
      );
      const img = el("img");
      img.alt = "배경 PDF와 생성한 손글씨 획 미리보기";
      out.append(chooser, img);
      let previewEpoch = 0;
      const render = async () => {
        const epoch = ++previewEpoch;
        const blob = await (
          await api(`exports/${r.export_id}/preview/${chooser.value}`)
        ).blob();
        if (epoch !== previewEpoch || !img.isConnected) return;
        img.src = blobUrl("result:page", blob);
      };
      chooser.onchange = () => action(render);
      await render();
      status(
        "손글씨 파일을 만들었습니다. 생성 보고서와 미리보기를 확인하세요.",
      );
    } finally {
      $("#write").disabled = false;
    }
  });
$("#replay").onclick = () => {
  stopReplay();
  const lines = [...document.querySelectorAll("#viewer polyline")];
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
  status("저장된 획 순서 재생입니다. 녹음 시간과 동기화된 재생은 아닙니다.");
};
action(refresh);
window.addEventListener("beforeunload", () => clearUrls(""));
