import { createI18n } from "vue-i18n";
import { en } from "./en";

export type Messages = typeof en;

export const i18n = createI18n<[Messages], "en", false>({
  legacy: false,
  locale: "en",
  fallbackLocale: "en",
  messages: { en },
});
