// --- Helpers ---------------------------------------------------------------
const $ = (q) => document.querySelector(q);
const bar = $("#bar");
const drop = $("#drop");
const fileInput = $("#file");
const browseBtn = $("#browseBtn");
const filemeta = $("#filemeta");
const chipsWrap = $("#chips");
const targetSelect = $("#targetSelect");
const convertBtn = $("#convertBtn");
const resultBox = $("#result");

const IMAGE_IN = ["jpg","jpeg","png","webp","gif","tiff","bmp","ico","pdf"];
const AV_IN     = ["mp3","wav","aac","flac","ogg","mp4","mkv","mov","webm"];
const DOC_IN    = ["pdf","doc","docx","ppt","pptx","xls","xlsx","odt","odp","ods","rtf","txt"];
const IMAGE_OUT = IMAGE_IN;
const AV_OUT    = AV_IN;
const DOC_OUT   = ["pdf","docx","xlsx","pptx","odt","ods","odp"];

function extOf(name){ const m = /\.[^.]+$/.exec(name||""); return m ? m[0].slice(1).toLowerCase() : ""; }
function humanSize(n){ if(!n && n!==0) return ""; const u=["B","KB","MB","GB"]; let i=0; while(n>=1024&&i<u.length-1){n/=1024;i++} return `${n.toFixed(n<10&&i?1:0)} ${u[i]}`; }

// Suggestions per category
const SUGGEST = {
  image: ["jpg","png","webp","pdf"],
  video: ["mp4","webm","mov","mp3"], // mp3 = extract audio
  audio: ["mp3","wav","aac","flac"],
  doc:   ["pdf","docx","pptx","xlsx"]
};

function guessCategory(ext){
  if(IMAGE_IN.includes(ext)) return "image";
  if(AV_IN.includes(ext)){
    // split audio vs video for smarter suggestions
    if(["mp4","mkv","mov","webm"].includes(ext)) return "video";
    return "audio";
  }
  if(DOC_IN.includes(ext)) return "doc";
  return "doc";
}

function allTargetsFor(cat){
  if(cat==="image") return IMAGE_OUT;
  if(cat==="video"||cat==="audio") return AV_OUT;
  return DOC_OUT;
}

function setSuggestions(cat){
  chipsWrap.innerHTML = "";
  const opts = allTargetsFor(cat);
  // populate <select> (all formats)
  targetSelect.innerHTML = opts.map(o=>`<option value="${o}">${o.toUpperCase()}</option>`).join("");

  // chips = top formats for that category
  const top = SUGGEST[cat] || [];
  top.forEach(fmt=>{
    if(!opts.includes(fmt)) return;
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "chip";
    chip.textContent = fmt.toUpperCase();
    chip.addEventListener("click", ()=>{
      [...chipsWrap.querySelectorAll(".chip")].forEach(c=>c.classList.remove("active"));
      chip.classList.add("active");
      targetSelect.value = fmt;
      convertBtn.disabled = !fileInput.files.length;
    });
    chipsWrap.appendChild(chip);
  });

  // select first chip by default
  const first = chipsWrap.querySelector(".chip");
  if(first){ first.click(); }
}

// --- Drag & drop -----------------------------------------------------------
["dragenter","dragover"].forEach(ev => drop.addEventListener(ev, e => {e.preventDefault(); drop.classList.add("drag");}));
["dragleave","drop"].forEach(ev => drop.addEventListener(ev, e => {e.preventDefault(); drop.classList.remove("drag");}));
drop.addEventListener("drop", (e)=>{
  const f = e.dataTransfer.files?.[0];
  if(f){ fileInput.files = e.dataTransfer.files; onFilePicked(f); }
});
browseBtn.addEventListener("click", ()=> fileInput.click());
fileInput.addEventListener("change", ()=> {
  const f = fileInput.files?.[0];
  if(f) onFilePicked(f);
});

function onFilePicked(file){
  resultBox.innerHTML = "";
  bar.style.width = "0%";
  const ext = extOf(file.name);
  const cat = guessCategory(ext);
  setSuggestions(cat);
  filemeta.textContent = `${file.name} — ${ext.toUpperCase()} • ${humanSize(file.size)}`;
  convertBtn.disabled = false;
}

// --- Convert action --------------------------------------------------------
convertBtn.addEventListener("click", async ()=>{
  const file = fileInput.files?.[0];
  if(!file){ toast("Please choose a file first.", true); return; }
  const target = targetSelect.value;
  if(!target){ toast("Please choose a target format.", true); return; }

  resultBox.innerHTML = "";
  bar.style.width = "0%";

  const fd = new FormData();
  fd.append("file", file);
  fd.append("target", target);

  const xhr = new XMLHttpRequest();
  xhr.open("POST", "/api/convert");
  xhr.upload.onprogress = (ev) => {
    if (ev.lengthComputable) bar.style.width = Math.round((ev.loaded / ev.total) * 100) + "%";
  };
  xhr.onreadystatechange = () => {
    if (xhr.readyState === XMLHttpRequest.DONE) {
      try {
        const data = JSON.parse(xhr.responseText);
        if (xhr.status === 200) {
          resultBox.innerHTML = `
            <span class="pill ok">Done</span>
            <a class="link" href="${data.download}" download>Download ${escapeHtml(data.filename)}</a>`;
        } else {
          resultBox.innerHTML = `<span class="pill err">Error</span> ${escapeHtml(data.detail || xhr.responseText)}`;
        }
      } catch {
        resultBox.textContent = xhr.responseText || "Unknown error";
      }
    }
  };
  xhr.send(fd);
});

function toast(text, isErr=false){
  resultBox.innerHTML = `<span class="pill ${isErr?'err':'ok'}">${isErr?'Error':'Note'}</span> ${escapeHtml(text)}`;
}
function escapeHtml(s){
  return String(s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
}
