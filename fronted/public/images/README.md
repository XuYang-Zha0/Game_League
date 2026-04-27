# 图片资源目录约定

## 游戏 Logo

- `logos/games/cs2-logo.svg`
- `logos/games/valorant-logo.png`
- `logos/games/lol-logo.png`

## 游戏头图

- `covers/games/cs2-header.jpg`
- `covers/games/valorant-header.jpg`
- `covers/games/lol-header.jpg`

## 后续扩展（你后面会加）

- 选手图：`players/`
- 战队图：`teams/`
- 地图图：`maps/`

你现在可以把你给的 CS2 logo 原图直接覆盖到：

- `public/images/logos/games/cs2-logo.svg`（建议同名覆盖）
- 或改成 `cs2-logo.png`，再把 `src/App.vue` 里的路径同步改掉。
