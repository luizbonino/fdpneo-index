<template>
  <div class="page">
    <h1>{{ $t("common.notFound") }}</h1>
    <div>
      <router-link class="btn" :to="{ name: 'directory' }">{{ $t("common.home") }}</router-link>
    </div>
  </div>
</template>
