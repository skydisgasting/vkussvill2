const PAGE_SIZE = 24;

const loadMoreButton = document.getElementById("load-more-button");
const cardsGrid = document.getElementById("cards-grid");
const emptyState = document.getElementById("empty-state");
const statusText = document.getElementById("status-text");
const statProducts = document.getElementById("stat-products");
const statVariants = document.getElementById("stat-variants");
const statVisible = document.getElementById("stat-visible");
const template = document.getElementById("meal-card-template");

const state = {
  rows: [],
  visibleRows: [],
  limit: PAGE_SIZE,
  scrapedAt: "",
  productCount: 0,
  variantCount: 0,
};

loadMoreButton.addEventListener("click", () => {
  state.limit += PAGE_SIZE;
  renderCards();
});

loadData();

function loadData() {
  const data = window.MEALS_DATA;
  if (!data) {
    statusText.textContent = "Не удалось загрузить данные каталога.";
    emptyState.classList.remove("hidden");
    return;
  }

  state.rows = flattenProducts(data.products ?? []);
  state.scrapedAt = data.scraped_at ?? "";
  state.productCount = data.product_count ?? 0;
  state.variantCount = data.variant_count ?? state.rows.length;

  statProducts.textContent = formatNumber(state.productCount);
  statVariants.textContent = formatNumber(state.variantCount);

  render();
}

function flattenProducts(products) {
  const rows = [];

  for (const product of products) {
    for (const variant of product.variants ?? []) {
      const cleanWeight = normalizeWeightText(product.weight || "");
      const weightGrams = parseWeightToGrams(cleanWeight);
      const totalProtein = weightGrams ? Number(variant.protein) * weightGrams / 100 : null;
      const totalCalories = weightGrams ? Number(variant.calories) * weightGrams / 100 : null;
      const totalScore = totalProtein && totalCalories
        ? (totalProtein / totalCalories) * 100
        : null;

      rows.push({
        title: product.title,
        weight: cleanWeight,
        weightGrams,
        url: product.url,
        imageUrl: product.image_url || "",
        manufacturer: variant.manufacturer || "Без уточнения",
        protein: Number(variant.protein),
        fats: Number(variant.fats),
        carbs: Number(variant.carbs),
        calories: Number(variant.calories),
        totalProtein,
        totalCalories,
        totalScore,
      });
    }
  }

  return rows
    .filter((row) => row.totalProtein !== null && row.totalCalories !== null && row.totalProtein > 30)
    .sort(compareRowsByScore);
}

function render() {
  state.visibleRows = [...state.rows].sort(compareRowsByScore);
  statVisible.textContent = formatNumber(state.visibleRows.length);
  statusText.textContent = "Показаны только блюда с белком больше 30 г, отсортированные по КПД блюда.";

  renderCards();
}

function renderCards() {
  cardsGrid.innerHTML = "";

  const shownRows = state.visibleRows.slice(0, state.limit);
  emptyState.classList.toggle("hidden", state.visibleRows.length > 0);
  cardsGrid.classList.toggle("hidden", state.visibleRows.length === 0);

  for (const row of shownRows) {
    cardsGrid.append(createCard(row));
  }

  loadMoreButton.classList.toggle("hidden", state.visibleRows.length <= shownRows.length);
}

function createCard(row) {
  const node = template.content.firstElementChild.cloneNode(true);

  const imageLink = node.querySelector(".meal-card__image-link");
  const image = node.querySelector(".meal-card__image");
  const badge = node.querySelector(".meal-card__badge");
  const manufacturer = node.querySelector(".meal-card__manufacturer");
  const title = node.querySelector(".meal-card__title");
  const totals = node.querySelector(".meal-card__totals");
  const protein = node.querySelector(".meal-card__protein");
  const calories = node.querySelector(".meal-card__calories");
  const fats = node.querySelector(".meal-card__fats");
  const carbs = node.querySelector(".meal-card__carbs");
  const score = node.querySelector(".meal-card__score");
  const link = node.querySelector(".meal-card__link");

  imageLink.href = row.url;
  link.href = row.url;
  badge.textContent = row.weight || "Вес не указан";
  manufacturer.textContent = row.manufacturer;
  title.textContent = row.title;
  totals.textContent = formatTotals(row);
  protein.textContent = formatMetric(row.protein);
  calories.textContent = formatMetric(row.calories);
  fats.textContent = formatMetric(row.fats);
  carbs.textContent = formatMetric(row.carbs);
  score.textContent = row.totalScore !== null ? row.totalScore.toFixed(3) : "n/a";

  if (row.imageUrl) {
    image.src = row.imageUrl;
    image.alt = row.title;
  } else {
    image.alt = row.title;
  }

  return node;
}

function formatMetric(value) {
  return Number.isInteger(value) ? String(value) : value.toFixed(1);
}

function formatNumber(value) {
  return new Intl.NumberFormat("ru-RU").format(value);
}

function formatTotals(row) {
  if (row.totalProtein === null || row.totalCalories === null) {
    return "Общий белок и калории не рассчитаны: в весе нет точных граммов.";
  }

  return `Во всём блюде: ${formatMetric(row.totalProtein)} г белка, ${formatMetric(row.totalCalories)} ккал`;
}

function compareRowsByScore(left, right) {
  const leftScore = left.totalScore ?? -Infinity;
  const rightScore = right.totalScore ?? -Infinity;

  if (leftScore !== rightScore) {
    return rightScore - leftScore;
  }

  const leftCalories = left.totalCalories ?? Infinity;
  const rightCalories = right.totalCalories ?? Infinity;
  if (leftCalories !== rightCalories) {
    return leftCalories - rightCalories;
  }

  const leftProtein = left.totalProtein ?? -Infinity;
  const rightProtein = right.totalProtein ?? -Infinity;
  return rightProtein - leftProtein;
}

function parseWeightToGrams(weightText) {
  const normalized = String(weightText).replace(",", ".").toLowerCase();

  if (!normalized) {
    return null;
  }

  const kgMatch = normalized.match(/(\d+(?:\.\d+)?)\s*кг(?:\s|$)/);
  if (kgMatch) {
    return Number(kgMatch[1]) * 1000;
  }

  const gramMatch = normalized.match(/(\d+(?:\.\d+)?)\s*г(?:\s|$)/);
  if (gramMatch) {
    return Number(gramMatch[1]);
  }

  return null;
}

function normalizeWeightText(weightText) {
  const normalized = String(weightText).replace(/\s+/g, " ").trim();
  if (!normalized) {
    return "";
  }

  const markerIndex = normalized.search(/\s(?:Как приготовить|Важные детали|Способ приготовления)\b/i);
  const cut = markerIndex >= 0 ? normalized.slice(0, markerIndex) : normalized;

  const compact = cut.trim();
  if (!compact) {
    return "";
  }

  const kgMatch = compact.match(/(\d+(?:[.,]\d+)?)\s*кг/i);
  if (kgMatch) {
    return `${kgMatch[1].replace(".", ",")} кг`;
  }

  const gramMatch = compact.match(/(\d+(?:[.,]\d+)?)\s*г/i);
  if (gramMatch) {
    return `${gramMatch[1].replace(".", ",")} г`;
  }

  const piecesMatch = compact.match(/(\d+(?:[.,]\d+)?)\s*шт/i);
  if (piecesMatch) {
    return `${piecesMatch[1].replace(".", ",")} шт`;
  }

  return compact;
}
