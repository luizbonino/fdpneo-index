import { createApp } from "vue";
import { QueryClient, VueQueryPlugin } from "@tanstack/vue-query";
import App from "./App.vue";
import { router } from "./router";
import { i18n } from "./i18n";
import { api } from "./api";
import { apiUrl } from "./api/client";
import { initSession } from "./auth/session";
import "./composables/theme";
import "./styles/main.css";

const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 15_000, refetchOnWindowFocus: true, retry: 1 } },
});

/** Browsers offer to add the index as a search engine (OpenSearch, TASKS 9.7). */
function announceSearch() {
  const link = document.createElement("link");
  link.rel = "search";
  link.type = "application/opensearchdescription+xml";
  link.title = "FDPneo Index";
  link.href = apiUrl("/index/search/opensearch.xml");
  document.head.append(link);
}

async function start() {
  // Restore any session before routing, so admin pages resolve on reload.
  try {
    const info = await api.config();
    queryClient.setQueryData(["config"], info);
    await initSession(info);
    if (info.search) announceSearch();
  } catch {
    // The index is unreachable: the app still renders and shows the error per view.
  }
  createApp(App).use(i18n).use(router).use(VueQueryPlugin, { queryClient }).mount("#app");
}

void start();
