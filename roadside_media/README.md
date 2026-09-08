# 路侧摄像机媒体

本目录存放 CARLA 路侧高杆摄像机导出的连续帧，以及离线编码后的 MP4。
原始图片和 MP4 **不进入 Git**，也 **不得复制进 frontend Docker 镜像**。

## 原始帧

将 CARLA 导出的 PNG 放到：

```text
roadside_media/demo_14/frame_XXXXXX.png
roadside_media/demo_15/frame_XXXXXX.png
roadside_media/demo_19/frame_XXXXXX.png
```

当前序列文件名形如 `frame_000001.png`，分辨率 1920×1080，RGBA PNG。
不要按字符串顺序手工拼接；转码脚本会按帧号自然排序后生成 concat list。

## 转码

依赖本机 `ffmpeg` / `ffprobe`。不要删除原始图片。

```bash
# 在仓库根目录
./scripts/build_roadside_media.sh
```

默认输出（10 FPS、高度 720、H.264 / libx264、CRF 26、yuv420p、无音频、`+faststart`）：

```text
roadside_media/encoded/demo_14.mp4
roadside_media/encoded/demo_15.mp4
roadside_media/encoded/demo_19.mp4
```

可选环境变量：

| 变量 | 默认 | 说明 |
|------|------|------|
| `ROADSIDE_MEDIA_FPS` | `10` | 输出帧率 |
| `ROADSIDE_MEDIA_HEIGHT` | `720` | 输出高度，按比例缩放；`540` 对应 960×540 |
| `ROADSIDE_MEDIA_CRF` | `26` | x264 质量 |
| `ROADSIDE_MEDIA_PRESET` | `fast` | x264 preset |

三路视频同时解码若影响 3D 地图，可改用：

```bash
ROADSIDE_MEDIA_HEIGHT=540 ./scripts/build_roadside_media.sh
```

浏览器访问路径（开发与生产相同，相对 URL，不要写主机绝对路径）：

```text
/roadside-media/demo_14.mp4
/roadside-media/demo_15.mp4
/roadside-media/demo_19.mp4
```

## 开发环境

```bash
cd frontend
npm run dev
```

Vite 会把 `roadside_media/encoded` 映射到 `/roadside-media/`，不复制视频到 `frontend/public`。
可用 `ROADSIDE_MEDIA_ENCODED_DIR` 覆盖编码目录。

## Docker 部署

当前仓库只有 `compose.redis.yml`，没有正式 frontend compose。
构建 frontend 镜像时 **禁止** `COPY roadside_media`。生产用只读 bind mount：

```text
宿主机:
./roadside_media/encoded

          ↓ bind mount :ro

frontend 容器:
/usr/share/nginx/html/roadside-media
```

示例（未来增加 frontend 服务时）：

```bash
docker build -t citypulse-frontend ./frontend

docker run --rm -p 8080:80 \
  -v /path/to/citypulse-v2x-sim/roadside_media/encoded:/usr/share/nginx/html/roadside-media:ro \
  citypulse-frontend
```

未来 compose 片段：

```yaml
frontend:
  build: ./frontend
  volumes:
    - ./roadside_media/encoded:/usr/share/nginx/html/roadside-media:ro
```

Nginx 已开启静态 MP4 的 Range 请求、`video/mp4` MIME 和短缓存。
媒体路径是 `Browser → frontend Nginx → bind-mounted MP4`，不经过 Backend。
