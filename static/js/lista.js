(function () {
  const search = document.getElementById("card-search");
  const results = document.getElementById("search-results");
  const productInput = document.getElementById("card-product");
  const selected = document.getElementById("selected-card");
  const form = document.getElementById("add-line-form");
  const modal = document.getElementById("versions-modal");
  const modalGrid = document.getElementById("versions-grid");
  const modalCount = document.getElementById("versions-count");
  const modalClose = document.getElementById("versions-close");
  if (!search || !form) return;

  const gameId = form.dataset.gameId;
  let timer = null;
  let controller = null;
  let lastResults = [];
  let lastQuery = "";

  function hide() {
    results.classList.add("hidden");
    results.innerHTML = "";
  }

  function thumb(idProduct, extraClass) {
    const img = document.createElement("img");
    img.className = extraClass ? "card-thumb " + extraClass : "card-thumb";
    img.src = "/img/" + idProduct;
    img.alt = "";
    img.loading = "lazy";
    img.width = 36;
    img.height = 50;
    if (window.watchThumb) window.watchThumb(img, idProduct);
    return img;
  }

  function cardMeta(item) {
    if (item.set_label) return item.set_label;
    return item.id_expansion != null ? "Set #" + item.id_expansion : "Sin set";
  }

  function selectCard(item) {
    productInput.value = item.id_product;
    const label = document.createElement("span");
    label.textContent = item.name + " · " + cardMeta(item);
    selected.replaceChildren(thumb(item.id_product, "card-thumb-lg"), label);
    selected.classList.remove("muted");
    search.value = "";
    hide();
  }

  async function load(q) {
    if (controller) controller.abort();
    controller = new AbortController();
    const resp = await fetch(
      "/api/cards/search?q=" + encodeURIComponent(q) + "&game=" + encodeURIComponent(gameId),
      { signal: controller.signal }
    );
    const data = await resp.json();
    lastResults = data;
    lastQuery = q;
    return data;
  }

  function renderResults(data) {
    results.innerHTML = "";
    if (!data.length) {
      results.innerHTML = '<div class="search-item muted">Sin resultados</div>';
    }
    data.forEach(function (item) {
      const div = document.createElement("div");
      div.className = "search-item";
      const info = document.createElement("div");
      info.className = "search-info";
      const name = document.createElement("span");
      name.className = "search-name";
      name.textContent = item.name;
      const meta = document.createElement("span");
      meta.className = "search-meta";
      meta.textContent = cardMeta(item);
      info.append(name, meta);
      div.appendChild(thumb(item.id_product));
      div.appendChild(info);
      div.addEventListener("click", function () {
        selectCard(item);
      });
      results.appendChild(div);
    });
    results.classList.remove("hidden");
  }

  function closeModal() {
    if (modal) modal.classList.add("hidden");
  }

  function openModal() {
    if (!modal || !modalGrid) return;
    modalGrid.innerHTML = "";
    modalCount.textContent =
      lastResults.length === 1 ? "· 1 versión" : "· " + lastResults.length + " versiones";
    lastResults.forEach(function (item) {
      const card = document.createElement("div");
      card.className = "version-card";
      card.tabIndex = 0;
      const name = document.createElement("span");
      name.className = "version-name";
      name.textContent = item.name;
      const meta = document.createElement("span");
      meta.className = "version-meta";
      meta.textContent = cardMeta(item);
      card.append(thumb(item.id_product), name, meta);
      const choose = function () {
        selectCard(item);
        closeModal();
      };
      card.addEventListener("click", choose);
      card.addEventListener("keydown", function (event) {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          choose();
        }
      });
      modalGrid.appendChild(card);
    });
    modal.classList.remove("hidden");
  }

  search.addEventListener("input", function () {
    const q = search.value.trim();
    if (timer) clearTimeout(timer);
    if (q.length < 2) {
      hide();
      return;
    }
    timer = setTimeout(async function () {
      try {
        renderResults(await load(q));
      } catch (e) {
        hide();
      }
    }, 250);
  });

  search.addEventListener("keydown", async function (event) {
    if (event.key !== "Enter") return;
    event.preventDefault();
    if (timer) clearTimeout(timer);
    const q = search.value.trim();
    if (q.length < 2) return;
    try {
      if (q !== lastQuery) await load(q);
    } catch (e) {
      return;
    }
    if (lastResults.length) openModal();
  });

  if (modal) {
    modal.addEventListener("click", function (event) {
      if (event.target === modal) closeModal();
    });
    modalClose.addEventListener("click", closeModal);
    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape") closeModal();
    });
  }

  document.addEventListener("click", function (event) {
    if (!results.contains(event.target) && event.target !== search) hide();
  });

  form.addEventListener("submit", function (event) {
    if (!productInput.value) {
      event.preventDefault();
      alert("Selecciona primero una carta de la lista de resultados.");
    }
  });
})();
