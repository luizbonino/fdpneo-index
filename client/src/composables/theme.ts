/**
 * Light / dark / system theme. The choice is a per-browser convenience kept in
 * localStorage; "system" follows prefers-color-scheme.
 */

import { computed, ref, watchEffect } from "vue";

export type ThemeMode = "system" | "light" | "dark";
const KEY = "fdpindex.theme";

function stored(): ThemeMode {
  try {
    const value = window.localStorage.getItem(KEY);
    return value === "light" || value === "dark" ? value : "system";
  } catch {
    return "system";
  }
}

export const themeMode = ref<ThemeMode>(stored());

const media =
  typeof window !== "undefined" && typeof window.matchMedia === "function"
    ? window.matchMedia("(prefers-color-scheme: dark)")
    : null;
const systemDark = ref(media?.matches ?? false);
media?.addEventListener("change", (event) => (systemDark.value = event.matches));

/** Whether the dark theme is on: charts read their colours again when it changes. */
export const darkTheme = computed(
  () => themeMode.value === "dark" || (themeMode.value === "system" && systemDark.value),
);

watchEffect(() => {
  document.documentElement.classList.toggle("theme-dark", darkTheme.value);
});

export function cycleTheme(): void {
  const order: ThemeMode[] = ["system", "light", "dark"];
  themeMode.value = order[(order.indexOf(themeMode.value) + 1) % order.length] ?? "system";
  try {
    window.localStorage.setItem(KEY, themeMode.value);
  } catch {
    // Storage may be unavailable (private mode): the choice lasts for this page.
  }
}
