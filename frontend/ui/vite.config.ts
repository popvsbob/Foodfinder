import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// https://vite.dev/config/
export default defineConfig({
  plugins: [vue()],
  server: {
    // 开发服务器代理：把浏览器请求的 /api、/outputs 转发给服务器本地的后端(8100)。
    // 好处：前端代码全部使用相对路径，浏览器只需转发 5173 一个端口，
    // 不再需要单独转发后端端口（解决换端口后 localhost:8100 连不上的 Network Error）。
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8100',
        changeOrigin: true,
        // SSE 流式响应需要关闭缓冲，确保事件实时推送
        configure: (proxy) => {
          proxy.on('proxyRes', (proxyRes) => {
            proxyRes.headers['cache-control'] = 'no-cache'
            proxyRes.headers['x-accel-buffering'] = 'no'
          })
        },
      },
      '/outputs': {
        target: 'http://127.0.0.1:8100',
        changeOrigin: true,
      },
    },
  },
})
