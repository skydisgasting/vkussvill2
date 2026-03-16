var PAGE_SIZE = 24;

var loadMoreButton = document.getElementById("load-more-button");
var cardsGrid = document.getElementById("cards-grid");
var emptyState = document.getElementById("empty-state");
var emptyStateTitle = document.getElementById("empty-state-title");
var emptyStateText = document.getElementById("empty-state-text");
var statusText = document.getElementById("status-text");
var addressText = document.getElementById("address-text");
var statProducts = document.getElementById("stat-products");
var statAvailable = document.getElementById("stat-available");
var statVisible = document.getElementById("stat-visible");
var protein30Toggle = document.getElementById("protein-30-toggle");
var excludeSimpleToggle = document.getElementById("exclude-simple-toggle");
var meatlessOnlyToggle = document.getElementById("meatless-only-toggle");
var template = document.getElementById("meal-card-template");
var API_SCRIPT_URL = window.location.protocol === "file:"
  ? "http://127.0.0.1:8000/api/meals.js"
  : "/api/meals.js";

var state = {
  rows: [],
  visibleRows: [],
  limit: PAGE_SIZE,
  parsedAt: "",
  address: "",
  strategy: "",
  totalCatalogCount: 0,
  availableCount: 0,
  servedFromCache: false,
  refreshing: false,
  refreshStartedAt: "",
  refreshFinishedAt: "",
  refreshLastError: "",
  hasError: false,
  activeScript: null,
  loadTimeoutId: 0
};

window.__vkussvilMealsCallback__ = function (data) {
  clearPendingLoad();

  if (data && data.error) {
    applyLoadError(new Error(data.details || data.error));
    return;
  }

  state.rows = data.products || [];
  state.parsedAt = data.parsed_at || "";
  state.address = data.address || "";
  state.strategy = data.shop_strategy || "";
  state.totalCatalogCount = data.total_catalog_count || state.rows.length;
  state.availableCount = data.available_count || state.rows.length;
  state.servedFromCache = !!data.served_from_cache;
  state.refreshing = !!data.refreshing;
  state.refreshStartedAt = data.refresh_started_at || "";
  state.refreshFinishedAt = data.refresh_finished_at || "";
  state.refreshLastError = data.refresh_last_error || "";
  state.hasError = false;

  statProducts.textContent = formatNumber(state.totalCatalogCount);
  statAvailable.textContent = formatNumber(state.availableCount);

  render();
};

boot();

function boot() {
  try {
    ensureElements();

    loadMoreButton.addEventListener("click", function () {
      state.limit += PAGE_SIZE;
      renderCards();
    });

    protein30Toggle.addEventListener("change", function () {
      state.limit = PAGE_SIZE;
      render();
    });

    excludeSimpleToggle.addEventListener("change", function () {
      state.limit = PAGE_SIZE;
      render();
    });

    meatlessOnlyToggle.addEventListener("change", function () {
      state.limit = PAGE_SIZE;
      render();
    });

    window.addEventListener("error", function (event) {
      if (event && event.filename && event.filename.indexOf("api/meals.js") !== -1) {
        return;
      }
      showFatalError("Ошибка JavaScript: " + getErrorMessage(event.error || event.message));
    });

    loadData();
  } catch (error) {
    showFatalError("Не удалось инициализировать страницу: " + getErrorMessage(error));
  }
}

function ensureElements() {
  var missing = [];
  var required = {
    loadMoreButton: loadMoreButton,
    cardsGrid: cardsGrid,
    emptyState: emptyState,
    emptyStateTitle: emptyStateTitle,
    emptyStateText: emptyStateText,
    statusText: statusText,
    addressText: addressText,
    statProducts: statProducts,
    statAvailable: statAvailable,
    statVisible: statVisible,
    protein30Toggle: protein30Toggle,
    excludeSimpleToggle: excludeSimpleToggle,
    meatlessOnlyToggle: meatlessOnlyToggle,
    template: template
  };
  var key;

  for (key in required) {
    if (required.hasOwnProperty(key) && !required[key]) {
      missing.push(key);
    }
  }

  if (missing.length) {
    throw new Error("В DOM не найдены элементы: " + missing.join(", "));
  }
}

function loadData() {
  statusText.textContent = "Обновляем каталог и остатки ВкусВилл…";
  addressText.textContent = "Отправляем запрос к локальному серверу.";
  state.hasError = false;
  emptyStateTitle.textContent = "Подходящих блюд не найдено";
  emptyStateText.textContent = "Проверяем каталог и наличие по выбранному адресу.";

  statProducts.textContent = "0";
  statAvailable.textContent = "0";
  statVisible.textContent = "0";

  clearPendingLoad();
  requestDataByScript();
}

function requestDataByScript() {
  var script = document.createElement("script");
  var timeoutMs = 360000;

  script.src = API_SCRIPT_URL + "?ts=" + Date.now();
  script.async = true;
  script.onerror = function () {
    clearPendingLoad();
    applyLoadError(new Error("Браузер не смог загрузить /api/meals.js"));
  };

  state.activeScript = script;
  state.loadTimeoutId = window.setTimeout(function () {
    clearPendingLoad();
    applyLoadError(new Error("Сервер слишком долго отвечает на /api/meals.js"));
  }, timeoutMs);

  document.head.appendChild(script);
}

function clearPendingLoad() {
  if (state.loadTimeoutId) {
    window.clearTimeout(state.loadTimeoutId);
    state.loadTimeoutId = 0;
  }

  if (state.activeScript && state.activeScript.parentNode) {
    state.activeScript.parentNode.removeChild(state.activeScript);
  }

  state.activeScript = null;
}

function applyLoadError(error) {
  state.hasError = true;
  state.rows = [];
  state.visibleRows = [];
  statProducts.textContent = "0";
  statAvailable.textContent = "0";
  statVisible.textContent = "0";
  cardsGrid.innerHTML = "";
  addClass(cardsGrid, "hidden");
  removeClass(emptyState, "hidden");
  emptyStateTitle.textContent = "Не удалось загрузить карточки";
  emptyStateText.textContent = buildLoadErrorText(error);
  statusText.textContent = "Не удалось загрузить данные: " + getErrorMessage(error);
  addressText.textContent = buildLoadHint();
}

function render() {
  state.visibleRows = state.rows.slice().filter(matchesFilters).sort(compareRows);

  statVisible.textContent = formatNumber(state.visibleRows.length);
  statusText.textContent = buildStatusText();
  addressText.textContent = buildAddressText();

  renderCards();
}

function matchesFilters(row) {
  if (protein30Toggle.checked && !hasMoreThan30Protein(row)) {
    return false;
  }

  if (excludeSimpleToggle.checked && row.is_simple_dish) {
    return false;
  }

  if (meatlessOnlyToggle.checked && !row.is_meatless) {
    return false;
  }

  return true;
}

function renderCards() {
  var shownRows = state.visibleRows.slice(0, state.limit);
  var showEmptyState = state.hasError || state.visibleRows.length === 0;
  var index;

  cardsGrid.innerHTML = "";

  if (!state.hasError) {
    emptyStateTitle.textContent = "Подходящих блюд не найдено";
    emptyStateText.textContent = "По текущим данным нет блюд, которые одновременно подходят под выбранные фильтры и есть в наличии.";
  }

  toggleClass(emptyState, "hidden", !showEmptyState);
  toggleClass(cardsGrid, "hidden", showEmptyState);

  for (index = 0; index < shownRows.length; index += 1) {
    cardsGrid.appendChild(createCard(shownRows[index]));
  }

  toggleClass(loadMoreButton, "hidden", state.visibleRows.length <= shownRows.length);
}

function createCard(row) {
  var node = template.content.firstElementChild.cloneNode(true);
  var imageLink = node.querySelector(".meal-card__image-link");
  var image = node.querySelector(".meal-card__image");
  var badge = node.querySelector(".meal-card__badge");
  var manufacturer = node.querySelector(".meal-card__manufacturer");
  var title = node.querySelector(".meal-card__title");
  var nutrition = node.querySelector(".meal-card__nutrition");
  var price = node.querySelector(".meal-card__price");
  var quantity = node.querySelector(".meal-card__quantity");
  var proteinTotal = node.querySelector(".meal-card__protein-total");
  var caloriesTotal = node.querySelector(".meal-card__calories-total");
  var score = node.querySelector(".meal-card__score");
  var link = node.querySelector(".meal-card__link");

  imageLink.href = row.url;
  link.href = row.url;
  badge.textContent = row.weight || "Вес не указан";
  manufacturer.textContent = row.manufacturer || "Без уточнения";
  title.textContent = row.title;
  nutrition.textContent = formatNutritionPer100(row);
  nutrition.title = row.composition || "Состав не найден";
  price.textContent = formatPrice(row.price);
  quantity.textContent = row.stock_quantity ? row.stock_quantity + " шт" : (row.stock_text || "В наличии");
  proteinTotal.textContent = formatTotalProtein(row.total_protein);
  caloriesTotal.textContent = formatTotalCalories(row.total_calories);
  score.textContent = formatScore(row.total_score);

  if (row.image_url) {
    image.src = row.image_url;
    image.alt = row.title;
  } else {
    image.removeAttribute("src");
    image.alt = row.title;
  }

  return node;
}

function buildStatusText() {
  var updatedAt;

  if (!state.parsedAt) {
    return "Каталог загружен.";
  }

  updatedAt = new Intl.DateTimeFormat("ru-RU", {
    dateStyle: "short",
    timeStyle: "medium"
  }).format(new Date(state.parsedAt));

  if (state.servedFromCache && state.refreshing) {
    return "Показана последняя сохранённая выгрузка от " + updatedAt + ". Сервер параллельно обновляет каталог и остатки. Сортировка по КПД блюда.";
  }

  if (state.servedFromCache) {
    return "Показана сохранённая выгрузка от " + updatedAt + ". Сортировка по КПД блюда.";
  }

  return "Каталог и остатки обновлены " + updatedAt + ". Сортировка по КПД блюда.";
}

function buildAddressText() {
  if (!state.address) {
    return "";
  }

  if (!state.strategy) {
    return "Адрес: " + state.address + ".";
  }

  return "Адрес: " + state.address + ". " + state.strategy;
}

function buildLoadHint() {
  if (window.location.protocol === "file:") {
    return "Страница открыта как файл. Запустите `python server.py` и откройте `http://127.0.0.1:8000`.";
  }

  return "Проверьте окно сервера: после открытия страницы там должен появиться GET /api/meals.js.";
}

function buildLoadErrorText(error) {
  if (window.location.protocol === "file:") {
    return "Страница открыта напрямую из файла. Если сервер уже запущен, откройте именно `http://127.0.0.1:8000`, а не локальный html-файл.";
  }

  return "Ошибка загрузки: " + getErrorMessage(error);
}

function showFatalError(message) {
  if (statusText) {
    statusText.textContent = message;
  }

  if (addressText) {
    addressText.textContent = buildLoadHint();
  }

  if (emptyStateTitle) {
    emptyStateTitle.textContent = "Ошибка интерфейса";
  }

  if (emptyStateText) {
    emptyStateText.textContent = message;
  }

  if (cardsGrid) {
    cardsGrid.innerHTML = "";
    addClass(cardsGrid, "hidden");
  }

  if (emptyState) {
    removeClass(emptyState, "hidden");
  }
}

function compareRows(left, right) {
  var leftScore = typeof left.total_score === "number" ? left.total_score : Number.NEGATIVE_INFINITY;
  var rightScore = typeof right.total_score === "number" ? right.total_score : Number.NEGATIVE_INFINITY;
  var leftCalories;
  var rightCalories;
  var leftProtein;
  var rightProtein;

  if (leftScore !== rightScore) {
    return rightScore - leftScore;
  }

  leftCalories = typeof left.total_calories === "number" ? left.total_calories : Number.POSITIVE_INFINITY;
  rightCalories = typeof right.total_calories === "number" ? right.total_calories : Number.POSITIVE_INFINITY;
  if (leftCalories !== rightCalories) {
    return leftCalories - rightCalories;
  }

  leftProtein = typeof left.total_protein === "number" ? left.total_protein : Number.NEGATIVE_INFINITY;
  rightProtein = typeof right.total_protein === "number" ? right.total_protein : Number.NEGATIVE_INFINITY;
  if (leftProtein !== rightProtein) {
    return rightProtein - leftProtein;
  }

  return left.title.localeCompare(right.title, "ru");
}

function formatNumber(value) {
  return new Intl.NumberFormat("ru-RU").format(value);
}

function formatPrice(value) {
  if (typeof value !== "number") {
    return "Цена не найдена";
  }

  return new Intl.NumberFormat("ru-RU", {
    style: "currency",
    currency: "RUB",
    maximumFractionDigits: 0
  }).format(value);
}

function formatNutritionPer100(row) {
  if (
    typeof row.protein_per_100 !== "number" ||
    typeof row.fats_per_100 !== "number" ||
    typeof row.carbs_per_100 !== "number" ||
    typeof row.calories_per_100 !== "number"
  ) {
    return "КБЖУ на 100 г: нет данных";
  }

  return "КБЖУ на 100 г: "
    + formatMetric(row.calories_per_100)
    + " ккал • Б "
    + formatMetric(row.protein_per_100)
    + " • Ж "
    + formatMetric(row.fats_per_100)
    + " • У "
    + formatMetric(row.carbs_per_100);
}

function formatTotalProtein(value) {
  if (typeof value !== "number") {
    return "Нет данных";
  }

  return formatMetric(value) + " г";
}

function formatTotalCalories(value) {
  if (typeof value !== "number") {
    return "Нет данных";
  }

  return formatMetric(value) + " ккал";
}

function formatScore(value) {
  if (typeof value !== "number") {
    return "Нет данных";
  }

  return value.toFixed(3);
}

function formatMetric(value) {
  if (Math.round(value * 10) / 10 === Math.round(value)) {
    return String(Math.round(value));
  }

  return value.toFixed(1);
}

function hasMoreThan30Protein(row) {
  return typeof row.total_protein === "number" && row.total_protein > 30;
}

function getErrorMessage(error) {
  if (!error) {
    return "Неизвестная ошибка";
  }

  if (typeof error === "string") {
    return error;
  }

  if (error.message) {
    return error.message;
  }

  try {
    return String(error);
  } catch (stringifyError) {
    return "Неизвестная ошибка";
  }
}

function toggleClass(node, className, shouldAdd) {
  if (!node) {
    return;
  }

  if (shouldAdd) {
    addClass(node, className);
  } else {
    removeClass(node, className);
  }
}

function addClass(node, className) {
  if (node && !hasClass(node, className)) {
    node.className += (node.className ? " " : "") + className;
  }
}

function removeClass(node, className) {
  var pattern;

  if (!node) {
    return;
  }

  pattern = new RegExp("(^|\\s)" + className + "(?=\\s|$)", "g");
  node.className = node.className.replace(pattern, " ").replace(/\s+/g, " ").replace(/^\s+|\s+$/g, "");
}

function hasClass(node, className) {
  return (" " + node.className + " ").indexOf(" " + className + " ") >= 0;
}
