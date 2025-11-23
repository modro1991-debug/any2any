const form = document.getElementById("form");
const bar = document.getElementById("bar");
const msg = document.getElementById("msg");

form.addEventListener("submit", (e) => {
  e.preventDefault();
  msg.innerHTML = "";
  bar.style.width = "0%";

  const file = document.getElementById("file").files[0];
  const target = document.getElementById("target").value;
  if (!file) return;

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
          msg.innerHTML = `<div class="pill success">Done</div> <a href="${data.download}" download>Download ${data.filename}</a>`;
        } else {
          msg.innerHTML = `<div class="pill error">Error</div> ${data.detail || xhr.responseText}`;
        }
      } catch { msg.textContent = xhr.responseText || "Unknown error"; }
    }
  };
  xhr.send(fd);
});
