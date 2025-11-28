(() => {
  const $ = (q) => document.querySelector(q);

  // Elements
  const drop = $("#drop");
  const fileInput = $("#file");
  const filemeta = $("#filemeta");
  const chipsWrap = $("#chips");
  const targetSelect = $("#targetSelect");
  const convertBtn = $("#convertBtn");
  const bar = $("#bar");
  const barInd = $("#barInd");
  const statusLine = $("#status");
  const resultBox = $("#result");

  // Supported formats for v1 (front-end view)
  const IMAGE_IN = ["jpg", "jpeg", "png", "webp", "tiff", "bmp"];
  const DOC_IN = ["pdf"];

  function extOf(name) {
    const m = /\.[^.]+$/.exec(name || "");
    return m ? m[0].slice(1).toLowerCase() : "";
  }

  function humanSize(n) {
    if (!n && n !== 0) return "";
    const u = ["B", "KB", "MB", "GB"];
    let i = 0;
    while (n >= 1024 && i < u.length - 1) {
      n /= 1024;
      i++;
    }
    return `${n.toFixed(n < 10 && i ? 1 : 0)} ${u[i]}`;
  }

  function guessCategory(ext) {
    if (DOC_IN.includes(ext)) return "doc";
    if (IMAGE_IN.includes(ext)) return "image";
    return "doc";
  }

  // Suggested targets based on v1 feature set
  function suggestedTargets(ext) {
    if (IMAGE_IN.includes(ext)) {
      // image -> PDF or DOCX
      return ["pdf", "docx"];
    }
    if (ext === "pdf") {
      // PDF -> images or DOCX
      return ["jpg", "png", "webp", "docx"];
    }
    return [];
  }

  function allTargetsFor(ext) {
    if (IMAGE_IN.includes(ext)) {
      return ["pdf", "docx"];
    }
    if (ext === "pdf") {
      return ["jpg", "png", "webp", "docx"];
    }
    return [];
  }

  function enableConvertIfReady() {
    const hasFile = !!fileInput.files?.length;
    const hasTarget = !!targetSelect.value;
    convertBtn.disabled = !(hasFile && hasTarget);
  }

  function setSuggestions(ext) {
    chipsWrap.innerHTML = "";
    const opts = allTargetsFor(ext);
    targetSelect.innerHTML = opts
      .map((o) => `<option value="${o}">${o.toUpperCase()}</option>`)
      .join("");

    const top = suggestedTargets(ext).filter((x) => opts.includes(x));
    top.forEach((fmt) => {
      const chip = document.createElement("button");
      chip.type = "button";
      chip.className = "chip";
      chip.textContent = fmt.toUpperCase();
      chip.addEventListener("click", () => {
        [...chipsWrap.querySelectorAll(".chip")].forEach((c) =>
          c.classList.remove("active")
        );
        chip.classList.add("active");
        targetSelect.value = fmt;
        enableConvertIfReady();
      });
      chipsWrap.appendChild(chip);
    });

    if (targetSelect.options.length > 0) targetSelect.selectedIndex = 0;
    const firstChip = chipsWrap.querySelector(".chip");
    if (firstChip) firstChip.click();

    enableConvertIfReady();
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"]/g, (c) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
    }[c]));
  }

  // ---------- UI events ----------
  if (!drop || !fileInput || !convertBtn || !targetSelect) {
    console.error("Any2Any: missing core elements in DOM");
    return;
  }

  targetSelect.addEventListener("change", enableConvertIfReady);

  // Drag & drop
  ["dragenter", "dragover"].forEach((ev) =>
    drop.addEventListener(ev, (e) => {
      e.preventDefault();
      drop.classList.add("drag");
    })
  );
  ["dragleave", "drop"].forEach((ev) =>
    drop.addEventListener(ev, (e) => {
      e.preventDefault();
      drop.classList.remove("drag");
    })
  );
  drop.addEventListener("drop", (e) => {
    const f = e.dataTransfer.files?.[0];
    if (f) {
      fileInput.files = e.dataTransfer.files;
      onFilePicked(f);
    }
  });

  // Ensure clicking on the visible button also opens dialog (backup)
  const chooseBtn = document.querySelector(".cta");
  if (chooseBtn) {
    chooseBtn.addEventListener("click", () => {
      fileInput?.click();
    });
  }

  // File picker change
  fileInput.addEventListener("change", () => {
    const f = fileInput.files?.[0];
    if (!f) return;
    onFilePicked(f);
  });

  function onFilePicked(file) {
    resultBox.innerHTML = "";
    statusLine.textContent = "";
    bar.style.width = "0%";
    barInd.style.display = "none";
    barInd.classList.remove("indeterminate");

    const ext = extOf(file.name);
    const cat = guessCategory(ext);
    console.log("Picked file:", file.name, "ext:", ext, "cat:", cat);

    setSuggestions(ext);

    filemeta.textContent = `${file.name} — ${ext.toUpperCase()} • ${humanSize(
      file.size
    )}`;
    enableConvertIfReady();
  }

  // ---------- Convert click ----------
  convertBtn.addEventListener("click", () => {
    if (convertBtn.disabled) {
      console.log("Convert clicked but disabled");
      return;
    }
    const file = fileInput.files?.[0];
    const target = targetSelect.value;
    if (!file || !target) {
      console.log("No file or target when clicking convert");
      return;
    }

    resultBox.innerHTML = "";
    statusLine.textContent = "";
    bar.style.width = "0%";
    barInd.style.display = "none";
    barInd.classList.remove("indeterminate");

    const srcExt = extOf(file.name);
    const cat = guessCategory(srcExt);
    console.log("Starting convert:", { srcExt, target, cat });

    statusLine.textContent = "Uploading…";
    const fd = new FormData();
    fd.append("file", file);
    fd.append("target", target);
    fd.append("category", cat);

    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/convert");

    xhr.onerror = () => {
      barInd.classList.remove("indeterminate");
      barInd.style.display = "none";
      resultBox.innerHTML =
        '<span class="pill err">Error</span> Network error while uploading.';
      statusLine.textContent = "";
    };
    xhr.onabort = () => {
      barInd.classList.remove("indeterminate");
      barInd.style.display = "none";
      resultBox.innerHTML =
        '<span class="pill err">Cancelled</span> Upload was aborted.';
      statusLine.textContent = "";
    };

    xhr.upload.onprogress = (ev) => {
      if (ev.lengthComputable) {
        const pct = Math.round((ev.loaded / ev.total) * 100);
        bar.style.width = pct + "%";
      }
    };
    xhr.upload.onload = () => {
      bar.style.width = "100%";
      statusLine.textContent = "Processing…";
      barInd.style.display = "block";
      barInd.classList.add("indeterminate");
    };

    xhr.onreadystatechange = () => {
      if (xhr.readyState === XMLHttpRequest.DONE) {
        barInd.classList.remove("indeterminate");
        barInd.style.display = "none";

        if (xhr.status === 200) {
          try {
            const data = JSON.parse(xhr.responseText);
            const t = data.process_time
              ? ` <span class="muted">(⏱ ${data.process_time}s)</span>`
              : "";
            statusLine.textContent = "Done.";
            resultBox.innerHTML = `<span class="pill ok">Done</span> <a class="link" href="${
              data.download
            }" download>Download ${escapeHtml(
              data.filename
            )}</a>${t}`;
          } catch (e) {
            console.error("Failed to parse JSON response", e, xhr.responseText);
            statusLine.textContent = "Done.";
            resultBox.textContent = "Download ready.";
          }
        } else {
          let msg = xhr.responseText || `HTTP ${xhr.status}`;
          try {
            const data = JSON.parse(xhr.responseText);
            msg = data.detail || msg;
          } catch {}
          console.error("Server error:", xhr.status, msg);
          resultBox.innerHTML = `<span class="pill err">Error</span> ${escapeHtml(
            msg
          )}`;
          statusLine.textContent = "";
        }
      }
    };

    xhr.send(fd);
  });

  // ---------- Cookie banner ----------
  const cb = document.getElementById("cookieBanner");
  const btnA = document.getElementById("cookieAccept");
  const btnD = document.getElementById("cookieDecline");

  function setCookie(name, value, days) {
    const maxAge = days * 24 * 60 * 60;
    document.cookie = `${name}=${encodeURIComponent(
      value
    )}; Max-Age=${maxAge}; Path=/; SameSite=Lax`;
  }
  function getCookie(name) {
    return document.cookie
      .split(";")
      .map((s) => s.trim())
      .find((s) => s.startsWith(name + "="))
      ?.split("=")[1];
  }
  function showBannerIfNeeded() {
    if (!getCookie("consent") && cb) {
      cb.classList.add("show");
      cb.setAttribute("aria-hidden", "false");
    }
  }
  btnA?.addEventListener("click", () => {
    setCookie("consent", "accept", 180);
    cb?.classList.remove("show");
    cb?.setAttribute("aria-hidden", "true");
  });
  btnD?.addEventListener("click", () => {
    cb?.classList.remove("show");
    cb?.setAttribute("aria-hidden", "true");
  });
  window.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && cb?.classList.contains("show")) btnD?.click();
  });
  showBannerIfNeeded();
})();
