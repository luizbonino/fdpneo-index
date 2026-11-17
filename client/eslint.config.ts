import { defineConfigWithVueTs, vueTsConfigs } from "@vue/eslint-config-typescript";
import pluginVue from "eslint-plugin-vue";
import skipFormatting from "@vue/eslint-config-prettier/skip-formatting";

export default defineConfigWithVueTs(
  {
    name: "app/files-to-lint",
    files: ["**/*.{ts,mts,tsx,vue}"],
  },
  {
    name: "app/files-to-ignore",
    ignores: ["**/dist/**", "**/coverage/**", "**/node_modules/**", "src/api/schema.ts"],
  },
  ...pluginVue.configs["flat/recommended"],
  vueTsConfigs.recommendedTypeChecked,
  skipFormatting,
  {
    rules: {
      "vue/multi-word-component-names": "warn",
      "@typescript-eslint/explicit-module-boundary-types": "off",
      "@typescript-eslint/no-floating-promises": ["error", { ignoreVoid: true }],
    },
  },
);
