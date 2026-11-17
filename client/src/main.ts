import { createApp } from "vue";
import { QueryClient, VueQueryPlugin } from "@tanstack/vue-query";
import App from "./App.vue";
import { router } from "./router";
import { i18n } from "./i18n";
import { api } from "./api";
import { initSession } from "./auth/session";
import "./composables/theme";
import "./styles/main.css";

const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 15_000, refetchOnWindowFocus: true, retry: 1 } },
});

async function start() {
  // Restore any session before routing, so admin pages resolve on reload.
  try {
    const info = await api.config();
    queryClient.setQueryData(["config"], info);
    await initSession(info);
  } catch {
    // The index is unreachable: the app still renders and shows the error per view.
  }
  createApp(App).use(i18n).use(router).use(VueQueryPlugin, { queryClient }).mount("#app");
}

void start();
