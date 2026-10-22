(function () {
  const search = document.getElementById("card-search");
  const results = document.getElementById("search-results");
  const productInput = document.getElementById("card-product");
  const selected = document.getElementById("selected-card");
  const form = document.getElementById("add-line-form");
  if (!search || !form) return;

  const gameId = form.dataset.gameId;
  let timer = null;
  let controller = null;

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

  search.addEventListener("input", function () {
    const q = search.value.trim();
    if (timer) clearTimeout(timer);
    if (q.length < 2) {
      hide();
      return;
    }
    timer = setTimeout(async function () {
      if (controller) controller.abort();
      controller = new AbortController();
      try {
        const resp = await fetch(
          "/api/cards/search?q=" + encodeURIComponent(q) + "&game=" + encodeURIComponent(gameId),
          { signal: controller.signal }
        );
        const data = await resp.json();
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
      } catch (e) {
        hide();
      }
    }, 250);
  });

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
