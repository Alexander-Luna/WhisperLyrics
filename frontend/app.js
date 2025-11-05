const uploadBtn = document.getElementById('uploadBtn');
const audioFile = document.getElementById('audioFile');
const audio = document.getElementById('audio');
const lyricsDiv = document.getElementById('lyrics');

let lyricsData = [];

uploadBtn.onclick = async () => {
  const file = audioFile.files[0];
  if (!file) return alert("Selecciona un archivo de audio");

  const formData = new FormData();
  formData.append("file", file);

  const res = await fetch("http://127.0.0.1:8000/transcribe/", {
    method: "POST",
    body: formData
  });

  const data = await res.json();
  if (data.error) return alert(data.error);

  // Cargar audio localmente
  const url = URL.createObjectURL(file);
  audio.src = url;

  // Leer el archivo .lrc del backend
  const lrcPath = `http://127.0.0.1:8000/uploads/${data.lrc_file}`;
  const lrcText = await fetch(lrcPath).then(r => r.text());
  lyricsData = parseLRC(lrcText);
  displayLyrics();
};

function parseLRC(text) {
  return text.split("\n").map(line => {
    const match = line.match(/\[(\d+):(\d+)\](.*)/);
    if (!match) return null;
    const [, min, sec, lyric] = match;
    return { time: parseInt(min) * 60 + parseInt(sec), text: lyric.trim() };
  }).filter(Boolean);
}

function displayLyrics() {
  lyricsDiv.innerHTML = lyricsData
    .map(l => `<div class="lyric-line">${l.text}</div>`)
    .join("");
}

audio.addEventListener("timeupdate", () => {
  const currentTime = audio.currentTime;
  for (let i = 0; i < lyricsData.length; i++) {
    if (currentTime >= lyricsData[i].time &&
        (i === lyricsData.length - 1 || currentTime < lyricsData[i + 1].time)) {
      highlightLine(i);
      break;
    }
  }
});

function highlightLine(index) {
  const lines = document.querySelectorAll(".lyric-line");
  lines.forEach((l, i) => {
    l.classList.toggle("active", i === index);
  });

  const active = lines[index];
  if (active) {
    active.scrollIntoView({ behavior: "smooth", block: "center" });
    anime({
      targets: active,
      scale: [1, 1.1, 1],
      duration: 600,
      easing: "easeOutElastic(1, .8)"
    });
  }
}
