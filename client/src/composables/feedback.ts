/**
 * App-wide feedback: transient toasts and a promise-based confirmation dialog.
 * `ToastHost` and `ConfirmHost` (mounted once in App.vue) render them.
 */

import { reactive, readonly } from "vue";

export interface Toast {
  id: number;
  message: string;
  tone: "ok" | "error";
}

const toasts = reactive<Toast[]>([]);
let nextId = 1;

export const toastList = readonly(toasts);

export function toast(message: string, tone: Toast["tone"] = "ok"): void {
  const id = nextId++;
  toasts.push({ id, message, tone });
  setTimeout(() => dismiss(id), tone === "error" ? 8000 : 4000);
}

export function dismiss(id: number): void {
  const index = toasts.findIndex((t) => t.id === id);
  if (index >= 0) toasts.splice(index, 1);
}

interface ConfirmRequest {
  message: string;
  confirmLabel: string;
  danger: boolean;
  resolve: (answer: boolean) => void;
}

export const confirmState = reactive<{ current: ConfirmRequest | null }>({ current: null });

export function confirm(
  message: string,
  options: { confirmLabel?: string; danger?: boolean } = {},
): Promise<boolean> {
  return new Promise((resolve) => {
    confirmState.current = {
      message,
      confirmLabel: options.confirmLabel ?? "Confirm",
      danger: options.danger ?? false,
      resolve,
    };
  });
}

export function answer(value: boolean): void {
  const current = confirmState.current;
  confirmState.current = null;
  current?.resolve(value);
}
