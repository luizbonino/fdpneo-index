<script setup lang="ts">
/**
 * Administrators: the index's saved searches (TASKS 10.3). Open one, feature it
 * on the search page, rename it, give its title in other languages (TASKS
 * 13.4), or delete it.
 */
import { ref } from "vue";
import { useI18n } from "vue-i18n";
import { useQuery, useQueryClient } from "@tanstack/vue-query";
import { ApiError } from "@/api/client";
import { confirm, toast } from "@/composables/feedback";
import { routeQuery, searchApi, type SavedSearch } from "@/search/api";
import { absoluteTime } from "@/utils/format";
import { currentLocale, LOCALES } from "@/i18n";

const { t } = useI18n();
const client = useQueryClient();
const saved = useQuery({ queryKey: ["saved-searches"], queryFn: searchApi.savedSearches });
const renaming = ref<Record<string, string>>({});
const translating = ref<string | null>(null);

function languageName(code: string): string {
  return new Intl.DisplayNames([currentLocale()], { type: "language" }).of(code) ?? code;
}

async function translate(item: SavedSearch, code: string, value: string) {
  const titles = { ...(item.titles ?? {}) };
  const text = value.trim();
  if ((titles[code] ?? "") === text) return;
  if (text) titles[code] = text;
  else delete titles[code];
  await change(item, { titles });
}

async function change(item: SavedSearch, update: Partial<SavedSearch>) {
  const next = { ...item, ...update };
  try {
    await searchApi.updateSavedSearch(item.id, {
      title: next.title,
      query: next.query,
      description: next.description,
      featured: next.featured,
      titles: next.titles ?? {},
      descriptions: next.descriptions ?? {},
    });
    await refresh();
  } catch (err) {
    toast(err instanceof ApiError ? err.message : (err as Error).message, "error");
  }
}

async function rename(item: SavedSearch) {
  const title = (renaming.value[item.id] ?? "").trim();
  if (!title || title === item.title) return;
  await change(item, { title });
  delete renaming.value[item.id];
}

async function remove(item: SavedSearch) {
  if (
    !(await confirm(t("saved.deleteConfirm", { title: item.title }), {
      confirmLabel: t("saved.delete"),
      danger: true,
    }))
  )
    return;
  await searchApi.deleteSavedSearch(item.id);
  await refresh();
}

async function refresh() {
  await client.invalidateQueries({ queryKey: ["saved-searches"] });
  await client.invalidateQueries({ queryKey: ["search-landing"] });
}
</script>

<template>
  <section class="card stack">
    <h2>{{ t("saved.heading") }}</h2>
    <p class="muted small">{{ t("saved.lead") }}</p>
    <p v-if="saved.isSuccess.value && !saved.data.value?.length" class="muted">
      {{ t("saved.none") }}
    </p>
    <table v-else-if="saved.data.value?.length" class="table">
      <thead>
        <tr>
          <th scope="col">{{ t("saved.title") }}</th>
          <th scope="col">{{ t("saved.featuredColumn") }}</th>
          <th scope="col">{{ t("saved.savedBy") }}</th>
          <th scope="col">
            <span class="visually-hidden">{{ t("saved.actions") }}</span>
          </th>
        </tr>
      </thead>
      <tbody>
        <template v-for="item in saved.data.value" :key="item.id">
          <tr>
            <td>
              <input
                class="input"
                :value="renaming[item.id] ?? item.title"
                maxlength="120"
                :aria-label="t('saved.renameLabel', { title: item.title })"
                @input="renaming[item.id] = ($event.target as HTMLInputElement).value"
                @change="rename(item)"
              />
            </td>
            <td>
              <input
                type="checkbox"
                :checked="item.featured"
                :aria-label="t('saved.featureLabel', { title: item.title })"
                @change="change(item, { featured: !item.featured })"
              />
            </td>
            <td class="small muted" :title="absoluteTime(item.createdAt)">{{ item.createdBy }}</td>
            <td class="row actions">
              <router-link
                class="btn ghost sm"
                :to="{ name: 'search', query: routeQuery(item.query) }"
              >
                {{ t("saved.open") }}
              </router-link>
              <button
                type="button"
                class="btn ghost sm"
                :aria-expanded="translating === item.id"
                :aria-label="t('saved.translationsOf', { title: item.title })"
                @click="translating = translating === item.id ? null : item.id"
              >
                {{ t("saved.translations") }}
              </button>
              <button type="button" class="btn ghost sm" @click="remove(item)">
                {{ t("saved.delete") }}
              </button>
            </td>
          </tr>
          <tr v-if="translating === item.id" class="languages">
            <td colspan="4">
              <p class="muted small">{{ t("saved.translationsHelp") }}</p>
              <div class="grid">
                <template v-for="code in LOCALES" :key="code">
                  <label :for="`saved-${item.id}-${code}`" class="small">{{
                    languageName(code)
                  }}</label>
                  <input
                    :id="`saved-${item.id}-${code}`"
                    class="input"
                    maxlength="120"
                    :lang="code"
                    :value="item.titles?.[code] ?? ''"
                    :placeholder="item.title"
                    :aria-label="
                      t('saved.titleIn', { title: item.title, language: languageName(code) })
                    "
                    @change="translate(item, code, ($event.target as HTMLInputElement).value)"
                  />
                </template>
              </div>
            </td>
          </tr>
        </template>
      </tbody>
    </table>
  </section>
</template>

<style scoped>
h2 {
  margin: 0;
}
.table {
  width: 100%;
}
.actions {
  justify-content: flex-end;
}
.grid {
  display: grid;
  grid-template-columns: max-content minmax(0, 1fr);
  gap: var(--fair-space-2) var(--fair-space-3);
  align-items: center;
}
</style>
