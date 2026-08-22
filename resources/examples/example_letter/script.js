(() => {
  const pages = [...document.querySelectorAll(".page")];
  const counter = document.getElementById("counter");
  const soundtrack = document.getElementById("soundtrack");
  const music = document.getElementById("music");
  let current = 0;

  function show(index) {
    current = (index + pages.length) % pages.length;
    pages.forEach((page, pageIndex) => page.classList.toggle("active", pageIndex === current));
    counter.textContent = `${current + 1} / ${pages.length}`;
  }

  document.getElementById("previous").addEventListener("click", () => show(current - 1));
  document.getElementById("next").addEventListener("click", () => show(current + 1));
  music.addEventListener("click", async () => {
    if (soundtrack.paused) {
      try {
        await soundtrack.play();
        music.textContent = "Pause music";
      } catch (_error) {
        music.textContent = "Music unavailable";
      }
    } else {
      soundtrack.pause();
      music.textContent = "Play music";
    }
  });
  show(0);
})();
