<script setup lang="ts">
import { computed } from "vue";
import { useRouter } from "vue-router";
import { useI18n } from "vue-i18n";
import { session, signOut } from "@/auth/session";
import { useCounts, useServiceInfo } from "@/composables/queries";
import { cycleTheme, themeMode } from "@/composables/theme";
import { apiUrl } from "@/api/client";
import ToastHost from "@/components/ToastHost.vue";
import ConfirmHost from "@/components/ConfirmHost.vue";
import AppMark from "@/components/AppMark.vue";
import { ENDONYMS, LOCALES, setLocale } from "@/i18n";

const { t, locale } = useI18n();
const router = useRouter();
const info = useServiceInfo();
const pending = useCounts("pending");
const pendingCount = computed(() =>
  session.isAdmin ? (pending.data.value?.entriesCount.ALL ?? 0) : 0,
);
const themeIcon = computed(() => ({ system: "◐", light: "☀", dark: "☾" })[themeMode.value]);

async function leave() {
  await signOut(info.data.value);
  void router.push({ name: "directory" });
}
</script>

<template>
  <a class="skip" href="#main">{{ t("app.skip") }}</a>
  <header class="topbar">
    <div class="inner">
      <router-link class="brand" :to="{ name: 'directory' }">
        <AppMark :size="34" />
        <span>
          <strong>{{ t("app.name") }}</strong>
          <span class="tagline">{{ t("app.tagline") }}</span>
        </span>
      </router-link>
      <nav class="nav" :aria-label="t('nav.main')">
        <router-link :to="{ name: 'directory' }">{{ t("nav.directory") }}</router-link>
        <router-link v-if="info.data.value?.search" :to="{ name: 'search' }">{{
          t("nav.search")
        }}</router-link>
        <router-link v-if="info.data.value?.sparql" :to="{ name: 'sparql' }">{{
          t("nav.sparql")
        }}</router-link>
        <router-link :to="{ name: 'statistics' }">{{ t("nav.statistics") }}</router-link>
        <router-link :to="{ name: 'register' }">{{ t("nav.register") }}</router-link>
        <template v-if="session.isAdmin">
          <router-link :to="{ name: 'approvals' }">
            {{ t("nav.approvals") }}
            <span v-if="pendingCount" class="count">{{ pendingCount }}</span>
          </router-link>
          <router-link :to="{ name: 'settings' }">{{ t("nav.settings") }}</router-link>
          <router-link :to="{ name: 'webhooks' }">{{ t("nav.webhooks") }}</router-link>
        </template>
      </nav>
      <div class="tools">
        <select
          class="input sm language"
          :value="locale"
          :aria-label="t('nav.language')"
          :title="t('nav.language')"
          @change="setLocale(($event.target as HTMLSelectElement).value)"
        >
          <option v-for="code in LOCALES" :key="code" :value="code" :lang="code">
            {{ ENDONYMS[code] }}
          </option>
        </select>
        <button
          class="btn ghost sm"
          :title="t('nav.theme')"
          :aria-label="t('nav.theme')"
          @click="cycleTheme"
        >
          {{ themeIcon }}
        </button>
        <template v-if="session.mode !== 'none'">
          <span class="muted small who">{{ session.name }}</span>
          <button class="btn sm" @click="leave">{{ t("nav.signOut") }}</button>
        </template>
        <router-link
          v-else-if="session.oidcAvailable || session.tokenAvailable"
          class="btn sm"
          :to="{ name: 'sign-in' }"
        >
          {{ t("nav.signIn") }}
        </router-link>
      </div>
    </div>
  </header>
  <main id="main">
    <router-view />
  </main>
  <footer class="footer">
    <span>{{ t("app.name") }}</span>
    <span v-if="info.data.value">{{
      t("footer.version", { version: info.data.value.version })
    }}</span>
    <a :href="apiUrl('/docs')">{{ t("footer.api") }}</a>
    <a
      v-if="info.data.value?.catalog"
      :href="info.data.value.catalog.feed"
      :title="t('footer.feedHelp')"
      >{{ t("footer.feed") }}</a
    >
  </footer>
  <ToastHost />
  <ConfirmHost />
</template>

<style scoped>
.skip {
  position: absolute;
  left: -9999px;
}
.skip:focus {
  left: 16px;
  top: 8px;
  z-index: 100;
  background: var(--fair-surface);
  padding: 8px 12px;
}
.topbar {
  background: var(--fair-surface);
  border-bottom: 1px solid var(--fair-border);
  position: sticky;
  top: 0;
  z-index: 20;
}
.inner {
  max-width: 1200px;
  margin: 0 auto;
  padding: 0 var(--fair-space-6);
  min-height: var(--fair-header-h);
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--fair-space-2) var(--fair-space-8);
}
.brand {
  display: flex;
  align-items: center;
  gap: 10px;
  color: var(--fair-text-strong);
}
.brand:hover {
  text-decoration: none;
}
.brand strong {
  display: block;
  line-height: 1.1;
}
.tagline {
  display: block;
  font-size: var(--fair-text-xs);
  color: var(--fair-text-muted);
}
.nav {
  display: flex;
  flex-wrap: wrap;
  gap: var(--fair-space-1);
  flex: 1;
}
.nav a {
  padding: 8px 12px;
  border-radius: var(--fair-radius-md);
  color: var(--fair-text);
  font-weight: var(--fair-weight-medium);
}
.nav a:hover {
  background: var(--fair-highlight);
  text-decoration: none;
}
.nav a.router-link-exact-active {
  color: var(--tool-accent);
  background: var(--tool-accent-tint);
}
.count {
  display: inline-grid;
  place-items: center;
  min-width: 20px;
  height: 20px;
  padding: 0 6px;
  margin-left: 4px;
  border-radius: var(--fair-radius-pill);
  background: var(--fair-warning);
  color: #fff;
  font-size: 11px;
}
.tools {
  display: flex;
  align-items: center;
  gap: var(--fair-space-2);
}
.who {
  max-width: 160px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
@media (max-width: 720px) {
  .inner {
    padding: 8px var(--fair-space-4);
  }
  .nav {
    order: 3;
    flex: 1 1 100%;
    flex-wrap: nowrap;
    overflow-x: auto;
    margin: 0 calc(-1 * var(--fair-space-2));
  }
  .nav a {
    white-space: nowrap;
  }
  .tools {
    margin-left: auto;
  }
  .tagline {
    display: none;
  }
}
.footer {
  max-width: 1200px;
  margin: 0 auto;
  padding: var(--fair-space-6);
  display: flex;
  gap: var(--fair-space-4);
  color: var(--fair-text-muted);
  font-size: var(--fair-text-sm);
  border-top: 1px solid var(--fair-separator);
}
</style>
