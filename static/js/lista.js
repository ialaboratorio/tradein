(function () {
  const search = document.getElementById("card-search");
  const results = document.getElementById("search-results");
  const gameInput = document.getElementById("card-game");
  const productInput = document.getElementById("card-product");
  const selected = document.getElementById("selected-card");
  const form = document.getElementById("add-line-form");
  if (!search || !form) return;

  let timer = null;
  let controller = null;

  function hide() {
    results.classList.add("hidden");
    results.innerHTML = "";
  }

  function selectCard(item) {
    gameInput.value = item.game_id;
    productInput.value = item.id_product;
    const price = item.has_price
      ? (item.low != null ? item.low.toFixed(2) + " €" : "solo foil")
      : "sin precio";
    selected.textContent = item.name + " · " + item.game_name + " · LOW " + price;
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
        const resp = await fetch("/api/cards/search?q=" + encodeURIComponent(q), {
          signal: controller.signal,
        });
        const data = await resp.json();
        results.innerHTML = "";
        if (!data.length) {
          results.innerHTML = '<div class="search-item muted">Sin resultados</div>';
        }
        data.forEach(function (item) {
          const div = document.createElement("div");
          div.className = "search-item";
          const priceLabel = item.low != null ? item.low.toFixed(2) + " €" : "—";
          div.textContent = item.name + " · " + item.game_name + " · LOW " + priceLabel;
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
    if (!productInput.value || !gameInput.value) {
      event.preventDefault();
      alert("Selecciona primero una carta de la lista de resultados.");
    }
  });
})();
