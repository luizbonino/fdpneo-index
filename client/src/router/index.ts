import {
  createRouter,
  createWebHistory,
  type RouteLocationNormalized,
  type RouteRecordRaw,
} from "vue-router";
import { session } from "@/auth/session";

declare module "vue-router" {
  interface RouteMeta {
    /** Only administrators may open this page. */
    admin?: boolean;
  }
}

const routes: RouteRecordRaw[] = [
  { path: "/", name: "directory", component: () => import("@/views/DirectoryView.vue") },
  {
    path: "/entries/:uuid",
    name: "entry",
    component: () => import("@/views/EntryView.vue"),
    props: true,
  },
  { path: "/register", name: "register", component: () => import("@/views/RegisterView.vue") },
  {
    path: "/admin/approvals",
    name: "approvals",
    component: () => import("@/views/ApprovalsView.vue"),
    meta: { admin: true },
  },
  {
    path: "/admin/settings",
    name: "settings",
    component: () => import("@/views/SettingsView.vue"),
    meta: { admin: true },
  },
  {
    path: "/admin/webhooks",
    name: "webhooks",
    component: () => import("@/views/WebhooksView.vue"),
    meta: { admin: true },
  },
  { path: "/sign-in", name: "sign-in", component: () => import("@/views/SignInView.vue") },
  { path: "/auth/callback", name: "callback", component: () => import("@/views/CallbackView.vue") },
  {
    path: "/:pathMatch(.*)*",
    name: "not-found",
    component: () => import("@/views/NotFoundView.vue"),
  },
];

export const router = createRouter({
  history: createWebHistory(),
  routes,
  scrollBehavior(to, _from, saved) {
    if (saved) return saved;
    if (to.hash) return { el: to.hash };
    return { top: 0 };
  },
});

/** Admin pages need an administrator; others are sent to sign in first. */
export function adminGuard(to: RouteLocationNormalized) {
  if (to.meta.admin && !session.isAdmin) {
    return { name: "sign-in", query: { returnTo: to.fullPath } };
  }
  return true;
}

router.beforeEach(adminGuard);
