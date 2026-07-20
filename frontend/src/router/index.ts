import { createRouter, createWebHistory } from 'vue-router'
import HomePage from '../pages/HomePage.vue'
import Amap3DTestPage from '../pages/Amap3DTestPage.vue'

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/',
      name: 'home',
      component: HomePage,
      meta: { title: '系统总览' },
    },
    {
      path: '/amap-3d-test',
      name: 'amap-3d-test',
      component: Amap3DTestPage,
      meta: { title: '高德 3D 验证', standalone: true },
    },
  ],
})
