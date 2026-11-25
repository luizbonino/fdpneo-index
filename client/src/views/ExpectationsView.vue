<script setup lang="ts">
/**
 * What this index expects of the FDPs it lists (TASKS 15.9): the network
 * profile, for maintainers to read before registering. With the generic
 * profile it says so: nothing beyond the FDP specification.
 */
import { computed } from "vue";
import { useI18n } from "vue-i18n";
import { useQuery } from "@tanstack/vue-query";
import { api, HEALTH_LEVELS } from "@/api";

const { t } = useI18n();
const view = useQuery({ queryKey: ["network-profile"], queryFn: api.networkProfile });
const profile = computed(() => view.data.value?.profile);
const levels = computed(() =>
  HEALTH_LEVELS.map((level) => ({ level, rules: profile.value?.levels?.[level] })).filter(
    (l) =>
      l.rules &&
      (l.rules.types?.length ?? 0) +
        (l.rules.requires?.length ?? 0) +
        (l.rules.recommends?.length ?? 0) >
        0,
  ),
);
const vocabularies = computed(() => Object.entries(profile.value?.vocabularies ?? {}));
</script>

<template>
  <div class="page narrow">
    <header class="page-head">
      <div>
        <h1>{{ t("expectations.title") }}</h1>
        <p class="lead">{{ t("expectations.lead") }}</p>
      </div>
    </header>
    <p v-if="view.isPending.value" class="muted">{{ t("common.loading") }}</p>
    <template v-else-if="view.data.value && profile">
      <section v-if="view.data.value.empty" class="card stack">
        <h2>{{ t("expectations.noneTitle") }}</h2>
        <p>{{ t("expectations.none") }}</p>
      </section>
      <template v-else>
        <section v-if="profile.network?.name || profile.network?.description" class="card stack">
          <h2>{{ profile.network?.name || t("expectations.network") }}</h2>
          <p v-if="profile.network?.description">{{ profile.network.description }}</p>
          <p class="small">
            <a v-if="profile.network?.homepage" :href="profile.network.homepage" rel="noopener">{{
              profile.network.homepage
            }}</a>
            <template v-if="profile.network?.homepage && profile.network?.contact"> · </template>
            <a v-if="profile.network?.contact" :href="profile.network.contact">{{
              profile.network.contact.replace(/^mailto:/, "")
            }}</a>
          </p>
        </section>

        <section class="card stack">
          <h2>{{ t("expectations.requirements") }}</h2>
          <p v-if="profile.reach">
            {{ t("expectations.reach", { level: t(`health.levels.${profile.reach}`) }) }}
          </p>
          <div v-if="levels.length" class="table-scroll">
            <table class="table">
              <thead>
                <tr>
                  <th scope="col">{{ t("health.level") }}</th>
                  <th scope="col">{{ t("expectations.requires") }}</th>
                  <th scope="col">{{ t("expectations.recommends") }}</th>
                  <th scope="col">{{ t("expectations.types") }}</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="l in levels" :key="l.level">
                  <th scope="row">{{ t(`health.levels.${l.level}`) }}</th>
                  <td>
                    <code v-for="p in l.rules?.requires ?? []" :key="p" class="term">{{ p }}</code>
                  </td>
                  <td>
                    <code v-for="p in l.rules?.recommends ?? []" :key="p" class="term">{{
                      p
                    }}</code>
                  </td>
                  <td>
                    <code v-for="p in l.rules?.types ?? []" :key="p" class="term">{{ p }}</code>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
          <template v-if="vocabularies.length">
            <h3>{{ t("expectations.vocabularies") }}</h3>
            <ul>
              <li v-for="[prop, prefixes] in vocabularies" :key="prop">
                <code>{{ prop }}</code>
                {{ t("expectations.fromVocab") }}
                <code v-for="p in prefixes" :key="p" class="term">{{ p }}</code>
              </li>
            </ul>
          </template>
          <template v-if="profile.shapes?.length">
            <h3>{{ t("expectations.shapes") }}</h3>
            <ul>
              <li v-for="s in profile.shapes" :key="s">
                <a :href="s" rel="noopener">{{ s }}</a>
              </li>
            </ul>
          </template>
        </section>

        <section v-if="profile.benchmarks?.length" class="card stack">
          <h2>{{ t("expectations.benchmarks") }}</h2>
          <p class="muted small">{{ t("expectations.benchmarksLead") }}</p>
          <ul>
            <li v-for="b in profile.benchmarks" :key="b">
              <a :href="b" rel="noopener">{{ b }}</a>
            </li>
          </ul>
        </section>
      </template>
      <p class="muted small">{{ t("expectations.declared") }}</p>
    </template>
  </div>
</template>

<style scoped>
.term {
  display: inline-block;
  margin: 0 var(--fair-space-1) var(--fair-space-1) 0;
  overflow-wrap: anywhere;
}
a {
  overflow-wrap: anywhere;
}
</style>
