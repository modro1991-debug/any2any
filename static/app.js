(() => {
  const $ = (q) => document.querySelector(q);

  // Elements
  const drop = $("#drop");
  const fileInput = $("#file");
  const filemeta = $("#filemeta");
  const chipsWrap = $("#chips");
  const targetSelect = $("#targetSelect");
  const convertBtn = $("#convertBtn");
  const localToggle = $("#localToggle");
  const bar = $("#bar");
  const barInd = $("#barInd");
  const statusLine = $("#status");
  const resultBox = $("#result");

  // Supported formats (must align with backend)
  const IMAGE_IN = ["jpg","jpeg","png","webp","gif","tiff","bmp","ico","pdf"];
  const AV_IN    = ["mp3","wav","aac","flac","ogg","mp4","mkv","mov","webm"];
  const DOC_IN   = ["pdf","doc","docx","ppt","pptx","xls","xlsx","odt","odp","ods","rtf","txt"];
  const DATA_IN  = ["csv","xlsx","txt","vcf","srt","vtt","json","yaml","yml"];

  const IMAGE_OUT = IMAGE_IN;
  const AV_OUT    = AV_IN;
  const DOC_OUT   = ["pdf","docx","pptx","xlsx","odt","odp","ods"];
  const DATA_OUT  = ["phonecsv","csv","vcf","srt","vtt","csv_from_json","json_from_csv","json_from_yaml","yaml_from_json"];

  // Local conversion availability
  const LOCAL_OK = {
    "pdf": new Set(["jpg","png","webp"]),            // all pages -> zip
    "jpg": new Set(["png","webp"]),
    "jpeg": new Set(["png","webp"]),
    "png": new Set(["jpg","webp"]),
    "webp": new Set(["jpg","png"]),
    "bmp": new Set(["jpg","png","webp"]),
    "gif": new Set(["jpg","png","webp"]),
    "tiff": new Set(["jpg","png","webp"]),
    "ico": new Set(["png","webp"]),
  };

  // Helpers
  function extOf(name){ const m=/\.[^.]+$/.exec(name||""); return m?m[0].slice(1).toLowerCase():""; }
  function humanSize(n){ if(!n&&n!==0) return ""; const u=["B","KB","MB","GB"]; let i=0; while(n>=1024&&i<u.length-1){n/=1024;i++} return `${n.toFixed(n<10&&i?1:0)} ${u[i]}`; }
  function guessCategory(ext){
    if (ext === "pdf") return "doc"; // so DOCX shows; we still allow PDF->images in local mode
    if (IMAGE_IN.includes(ext)) return "image";
    if (AV_IN.includes(ext)) return ["mp4","mkv","mov","webm"].includes(ext) ? "video" : "audio";
    if (DOC_IN.includes(ext)) return "doc";
    if (DATA_IN.includes(ext)) return "data";
    return "doc";
  }
  function suggestedTargets(ext, cat){
    if (ext === "pdf") return ["jpg","png","webp","docx"];
    if (cat === "image") return ["jpg","png","webp","pdf"].filter(x => x !== ext);
    if (cat === "video") return ["mp4","webm","mp3"];
    if (cat === "audio") return ["mp3","wav","aac","flac"].filter(x => x !== ext);
    if (cat === "doc")   return ["pdf","docx","pptx","xlsx","odt","odp","ods"].filter(x => x !== ext);
    if (cat === "data")  return ["phonecsv","csv","vcf","vtt","srt","csv_from_json","json_from_csv"];
    return [];
  }
  function allTargetsFor(ext, cat){
    if (ext === "pdf"){
      const imgs = IMAGE_OUT.filter(x => x !== "pdf");
      return [...new Set([...imgs, "docx"])];
    }
    if (cat === "image") return IMAGE_OUT.filter(x => x !== ext);
    if (cat === "video" || cat === "audio") return AV_OUT.filter(x => x !== ext);
    if (cat === "data") return DATA_OUT;
    return DOC_OUT.filter(x => x !== ext);
  }
  function enableConvertIfReady(){
    const hasFile = !!fileInput.files?.length;
    const hasTarget = !!targetSelect.value;
    convertBtn.disabled = !(hasFile && hasTarget);
  }
  function setSuggestions(ext, cat){
    chipsWrap.innerHTML = "";
    const opts = allTargetsFor(ext, cat);
    targetSelect.innerHTML = opts.map(o => `<option value="${o}">${o.toUpperCase()}</option>`).join("");

    const top = suggestedTargets(ext, cat).filter(x => opts.includes(x));
    top.forEach(fmt => {
      const chip = document.createElement("button");
      chip.type = "button";
      chip.className = "chip";
      chip.textContent = fmt.toUpperCase();
      chip.addEventListener("click", () => {
        [...chipsWrap.querySelectorAll(".chip")].forEach(c => c.classList.remove("active"));
        chip.classList.add("active");
        targetSelect.value = fmt;
        enableConvertIfReady();
      });
      chipsWrap.appendChild(chip);
    });

    // default selection
    if (targetSelect.options.length > 0) targetSelect.selectedIndex = 0;
    const firstChip = chipsWrap.querySelector(".chip");
    if (firstChip) firstChip.click();

    enableConvertIfReady();
  }
  function canDoLocal(srcExt, targetExt){ return LOCAL_OK[srcExt]?.has(targetExt) || false; }
  function note(t, err=false){ resultBox.innerHTML = `<span class="pill ${err?'err':'ok'}">${err?'Error':'Note'}</span> ${escapeHtml(t)}`; }
  function escapeHtml(s){return String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}

  // Local conversion using pdf.js + canvas + JSZip
  async function convertLocal(file, srcExt, targetExt){
    const PDFJS = window['pdfjsLib'];

    // PDF -> images (all pages) -> ZIP
    if (srcExt === "pdf" && ["jpg","png","webp"].includes(targetExt)){
      statusLine.textContent = "Preparing PDF…";
      const buf = await file.arrayBuffer();
      const pdf = await PDFJS.getDocument({ data: buf }).promise;
      const pages = pdf.numPages;
      const zip = new JSZip();
      let done = 0;

      for (let i = 1; i <= pages; i++){
        const page = await pdf.getPage(i);
        const viewport = page.getViewport({ scale: 2 });
        const canvas = document.createElement("canvas");
        const ctx = canvas.getContext("2d");
        canvas.width = viewport.width; canvas.height = viewport.height;
        await page.render({ canvasContext: ctx, viewport }).promise;

        const mime = {jpg:"image/jpeg", png:"image/png", webp:"image/webp"}[targetExt];
        const blob = await new Promise(res => canvas.toBlob(res, mime, mime === "image/jpeg" ? 0.92 : 0.95));
        zip.file(`page-${i}.${targetExt}`, blob);

        done++;
        const pct = Math.round((done/pages)*100);
        bar.style.width = pct + "%";
        statusLine.textContent = `Processing… ${pct}% (${done}/${pages})`;
      }
      const zipBlob = await zip.generateAsync({ type: "blob" });
      const url = URL.createObjectURL(zipBlob);
      const name = file.name.replace(/\.pdf$/i, `_${targetExt}.zip`);
      return { url, filename: name };
    }

    // Image -> image (jpg/png/webp)
    if (["jpg","jpeg","png","webp","gif","tiff","bmp","ico"].includes(srcExt) &&
        ["jpg","png","webp"].includes(targetExt)){
      statusLine.textContent = "Decoding image…";
      const bmp = await createImageBitmap(file);
      bar.style.width = "30%";
      const canvas = document.createElement("canvas");
      const ctx = canvas.getContext("2d");
      canvas.width = bmp.width; canvas.height = bmp.height;
      ctx.drawImage(bmp, 0, 0);
      bar.style.width = "60%";
      const mime = {jpg:"image/jpeg", png:"image/png", webp:"image/webp"}[targetExt];
      const blob = await new Promise(res => canvas.toBlob(res, mime, mime === "image/jpeg" ? 0.92 : 0.95));
      const url = URL.createObjectURL(blob);
      const base = file.name.replace(/\.[^.]+$/, "");
      bar.style.width = "100%";
      statusLine.textContent = "Done.";
      return { url, filename: `${base}.${targetExt}` };
    }

    throw new Error("Local conversion not supported for this format.");
  }

  // UI events
  targetSelect.addEventListener("change", enableConvertIfReady);

  // Drag & drop
  ["dragenter","dragover"].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.add("drag"); }));
  ["dragleave","drop"].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.remove("drag"); }));
  drop.addEventListener("drop", e => {
    const f = e.dataTransfer.files?.[0];
    if (f){ fileInput.files = e.dataTransfer.files; onFilePicked(f); }
  });

  // File picker
  fileInput.addEventListener("change", () => {
    const f = fileInput.files?.[0];
    if (f) onFilePicked(f);
  });

  function onFilePicked(file){
    // reset UI
    resultBox.innerHTML = "";
    statusLine.textContent = "";
    bar.style.width = "0%";
    barInd.style.display = "none";
    barInd.classList.remove("indeterminate");

    // populate targets
    const ext = extOf(file.name);
    const cat = guessCategory(ext);
    setSuggestions(ext, cat);

    // meta
    filemeta.textContent = `${file.name} — ${ext.toUpperCase()} • ${humanSize(file.size)}`;
    enableConvertIfReady();
  }

  // Convert click
  convertBtn.addEventListener("click", async () => {
    if (convertBtn.disabled) return;
    const file = fileInput.files?.[0];
    const target = targetSelect.value;
    if (!file || !target) return;

    // reset
    resultBox.innerHTML = "";
    statusLine.textContent = "";
    bar.style.width = "0%";
    barInd.style.display = "none";
    barInd.classList.remove("indeterminate");

    const srcExt = extOf(file.name);

    // Local path
    if (localToggle.checked && canDoLocal(srcExt, target)){
      try{
        const t0 = performance.now();
        const out = await convertLocal(file, srcExt, target);
        const elapsed = ((performance.now() - t0) / 1000).toFixed(2);
        resultBox.innerHTML = `<span class="pill ok">Done</span> <a class="link" href="${out.url}" download="${escapeHtml(out.filename)}">Download ${escapeHtml(out.filename)}</a> <span class="muted">(⏱ ${elapsed}s)</span>`;
        return;
      }catch(err){
        console.warn("Local conversion failed, falling back to server:", err);
      }
    }

    // Server path
    statusLine.textContent = "Uploading…";
    const fd = new FormData();
    fd.append("file", file);
    fd.append("target", target);
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/convert");

    xhr.upload.onprogress = (ev) => {
      if (ev.lengthComputable){
        bar.style.width = Math.round((ev.loaded/ev.total)*100) + "%";
      }
    };
    xhr.upload.onload = () => {
      bar.style.width = "100%";
      statusLine.textContent = "Processing…";
      barInd.style.display = "block";
      barInd.classList.add("indeterminate");
    };

    xhr.onreadystatechange = () => {
      if (xhr.readyState === XMLHttpRequest.DONE){
        barInd.classList.remove("indeterminate");
        barInd.style.display = "none";

        if (xhr.status === 200){
          try{
            const data = JSON.parse(xhr.responseText);
            const t = data.process_time ? ` <span class="muted">(⏱ ${data.process_time}s)</span>` : "";
            statusLine.textContent = "Done.";
            resultBox.innerHTML = `<span class="pill ok">Done</span> <a class="link" href="${data.download}" download>Download ${escapeHtml(data.filename)}</a>${t}`;
          }catch{
            statusLine.textContent = "Done.";
            resultBox.textContent = "Download ready.";
          }
        } else {
          try{
            const data = JSON.parse(xhr.responseText);
            resultBox.innerHTML = `<span class="pill err">Error</span> ${escapeHtml(data.detail || xhr.responseText)}`;
          }catch{
            resultBox.innerHTML = `<span class="pill err">Error</span> ${escapeHtml(xhr.responseText || "Unknown error")}`;
          }
          statusLine.textContent = "";
        }
      }
    };
    xhr.send(fd);
  });

  // Cookie banner (polite)
  const cb = document.getElementById('cookieBanner');
  const btnA = document.getElementById('cookieAccept');
  const btnD = document.getElementById('cookieDecline');
  function setCookie(name, value, days){
    const maxAge = days*24*60*60;
    document.cookie = `${name}=${encodeURIComponent(value)}; Max-Age=${maxAge}; Path=/; SameSite=Lax`;
  }
  function getCookie(name){
    return document.cookie.split(';').map(s=>s.trim()).find(s=>s.startsWith(name+'='))?.split('=')[1];
  }
  function showBannerIfNeeded(){ if(!getCookie('consent')){ cb.classList.add('show'); cb.setAttribute('aria-hidden','false'); } }
  btnA?.addEventListener('click', ()=>{ setCookie('consent','accept',180); cb.classList.remove('show'); cb.setAttribute('aria-hidden','true'); });
  btnD?.addEventListener('click', ()=>{ cb.classList.remove('show'); cb.setAttribute('aria-hidden','true'); });
  window.addEventListener('keydown', (e)=>{ if(e.key==='Escape' && cb.classList.contains('show')) btnD.click(); });
  showBannerIfNeeded();
})();
