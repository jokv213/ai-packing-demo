# AI梱包提案デモ（FastAPI + SQLite + Jinja2）

## 動作環境
- Python 3.11 以上（3.12 でも可）

## セットアップ
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 起動
```bash
./scripts/dev.sh
```

起動後にログに表示されたURLを開いてください。  
例: `http://127.0.0.1:8000` または `http://127.0.0.1:8001`

重要:
- `8000` が他プロセス（Dockerなど）で使用中の場合、`./scripts/dev.sh` は自動で `8001-8005` に切り替えます。
- ブラウザで `http://127.0.0.1:8000` を固定で開かず、**起動ログに表示されたURL** を必ず使ってください。

事前診断（推奨）:
```bash
./scripts/doctor.sh
```

起動後の状態確認:
```bash
./scripts/status.sh
```

停止:
```bash
./scripts/stop.sh
```

## 常駐起動（推奨）
ブラウザでの `ERR_CONNECTION_REFUSED` を避けるため、macOSの `launchd` 管理で常駐起動できます。

```bash
./scripts/service.sh start
./scripts/service.sh status
./scripts/service.sh logs
./scripts/service.sh stop
```

- デフォルトは `http://127.0.0.1:8000`
- `start` は `.venv` と依存を自動確認し、不足時はインストールします
- `status` は `orders` 画面のCSVインポートUIまで確認します

`./scripts/dev.sh` はデフォルトで `reload=off` で起動します（安定優先）。
ホットリロードが必要な場合は:
```bash
RELOAD=1 ./scripts/dev.sh
```

手動で起動する場合:
```bash
source .venv/bin/activate
uvicorn app.main:app --reload
```

## 初期データ投入
- 初回起動時に、DBが空なら `seed/*.csv` を自動投入します。
- 画面右上の `DBリセット` ボタンで、DB初期化 + seed再投入ができます（`POST /admin/reset`）。

## 主要画面
- Dashboard: `/`
- Orders: `/orders`
- Order Detail: `/orders/{order_id}`
- Packing Assistant: `/packing`, `/packing/{order_id}`
- Simulator: `/simulator`
- Masters: `/masters/skus`, `/masters/boxes`, `/masters/rates`, `/masters/prohibited`
- Logs: `/logs`

## CSVインポート
- 受注（Orders画面）: `orders.csv` と `order_items.csv` を同時アップロード
  - デフォルト: 同じ `order_id` のみ置換（既存の他注文は保持）
  - 「既存受注をすべて置き換える」にチェック時のみ全置換
- SKU: `POST /masters/skus/import`
- 箱: `POST /masters/boxes/import`
- 運賃: `POST /masters/rates/import`
- 同梱禁止: `POST /masters/prohibited/import`

## 補足
- 梱包推薦ロジックは `app/services/packing.py` に実装。
- seed投入処理は `app/services/seed.py` に実装。
- DB接続先は `DATABASE_URL` 環境変数で上書きできます（未指定時は `sqlite:///./app.db`）。

## デプロイ（クライアント共有用）
このリポジトリは `Dockerfile` を含んでいるため、Render / Railway にそのままデプロイできます。

### 1) Render（推奨）
1. GitHubにこのリポジトリをpush
2. Renderで `New +` -> `Blueprint` を選択
3. 対象リポジトリを選択（`render.yaml` が自動読込されます）
4. Deploy実行
5. 発行されたURLにアクセス

注意:
- SQLite (`app.db`) はコンテナ再作成時に消えるため、長期運用はPostgreSQL推奨です。

### 2) Railway
1. GitHubにこのリポジトリをpush
2. Railwayで `New Project` -> `Deploy from GitHub repo`
3. 対象リポジトリを選択（`railway.toml` + `Dockerfile` で起動）
4. Public Networkingを有効化し、発行URLにアクセス

### ローカルDocker実行
```bash
docker build -t ai-packing-demo .
docker run --rm -p 8000:8000 ai-packing-demo
```
`http://127.0.0.1:8000` を開いて確認できます。

## トラブルシュート
- `python3.11: command not found` の場合:
  - `python3 -m venv .venv` を使ってください。
- `address already in use` の場合:
  - `./scripts/dev.sh` は `PORT` 未指定時、`8000` が使用中なら `8001-8005` の空きポートへ自動で切り替えます。
  - 固定ポートで起動したい場合は `PORT=8001 ./scripts/dev.sh` を使ってください。
- `127.0.0.1:8000` で別アプリが表示される場合:
  - Dockerのコンテナが `8000` を掴んでいる可能性があります。
  - `docker ps` で `0.0.0.0:8000->8000/tcp` を確認し、不要なら `docker stop <CONTAINER_ID>` で停止してください。
  - もしくは `./scripts/dev.sh` が提示した `8001` 以降のURLを使ってください。
- `uvicorn --reload` でファイル監視系の Permission エラーが出る場合:
  - `RELOAD=1` 利用時に起きることがあります。まず `./scripts/dev.sh`（reload=off）で起動確認してください。
  - reloadを使う場合は `WATCHFILES_FORCE_POLLING=1 RELOAD=1 ./scripts/dev.sh` を使ってください。
- `.venv` が古い Python で作られている場合:
  - `./scripts/dev.sh` 実行時に自動で作り直します（Python 3.11+）。
- 原因を機械的に確認したい場合:
  - `./scripts/doctor.sh` を実行すると、Python/依存/ポート/URLの確認結果が表示されます。
