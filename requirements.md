# AI梱包提案デモ（社内向けWebアプリ）要件定義 v1

## 1. 目的（このデモで見せたい価値）
EC出荷業務で、以下を一つの画面体験としてデモできること。

1. **複数商品の最適同梱（箱サイズ提案）**
   - 受注（複数SKU）から、**最小〜最安の梱包箱**を提案し、同梱不可があれば自動で分割案を出す。
2. **新商品の配送料シミュレーション**
   - 新SKUの寸法/重量を入力すると、**想定箱・配送サイズ・送料**を即座に試算できる。
3. **現場での検品/梱包時の箱提案（Packing Assistant）**
   - オーダーIDを入力 → 推奨箱を提示 → 実際に使った箱を記録（推奨との差分理由もログ）できる。

> 注：本デモは「AIモデルの学習」よりも、**制約考慮・説明可能な推薦・ログ蓄積**を重視する。  
> 将来フェーズで学習を乗せる前提の“足場”として設計する。

---

## 2. スコープ
### 2.1 対象（in scope）
- マスタ（SKU・箱・配送料金表）のCRUDとCSVインポート
- 受注（Order/OrderItem）の閲覧と簡易ステータス
- 梱包提案（自動分割 + 箱推薦 + 送料試算）
- 検品/梱包アシスト画面（実績入力 + ログ）
- 簡易分析（推奨採用率、差し替え理由ランキング）

### 2.2 対象外（out of scope）
- ログイン/認証/権限管理
- 実在の配送会社API連携（運賃の自動取得）
- 送り状発行、ラベル印刷、実在WMS/OMS連携
- 3Dの配置図の完全再現（デモでは「入る/入らない判定 + 箱選定」を主目的）

---

## 3. 想定利用者（社内）
- **倉庫現場（梱包者）**：推奨箱を見て素早く箱を選ぶ。合わない場合は理由を選ぶだけ。
- **運用管理者**：箱コスト/送料を最適化。例外（入らない/差し替え）を見てマスタを改善。
- **商品登録担当**：新SKUの寸法・重量を入力し、送料レンジを見て販売判断に使う。

---

## 4. システム構成（Codexで一発実装しやすい前提）
### 4.1 推奨スタック（固定）
- Backend: **Python 3.11 + FastAPI**
- DB: **SQLite**
- ORM: **SQLAlchemy**
- Frontend: **サーバーサイドHTML（Jinja2）+ 最小限のJavaScript**
  - 理由：SPAより実装が短く、デモには十分。Codexで生成が安定しやすい。

### 4.2 起動方法（要件）
- `uvicorn app.main:app --reload` で起動
- 初回起動時、DBが空なら `seed/*.csv` を読み込み自動投入
- 画面から「DBを初期化してseedに戻す」ボタン（デモ用）を提供

---

## 5. データ（本来クライアントから貰う想定の情報と、ダミーデータ）
### 5.1 同梱するダミーデータ（CSV）
リポジトリに `seed/` を作り、以下のCSVを格納する。

- `seed/skus.csv`
- `seed/boxes.csv`
- `seed/shipping_rates.csv`
- `seed/orders.csv`
- `seed/order_items.csv`
- `seed/prohibited_group_pairs.csv`
- `seed/seed_recommendations.csv`（デモ用：初期の推奨結果を事前計算した例。アプリ側は再計算しても良い）

#### 5.1.1 skus.csv（SKUマスタ）
|列名|型|例|説明|
|---|---:|---|---|
|sku_id|string|SKU-TSHIRT-BLK-M|SKUキー（ユニーク）|
|name|string|Tシャツ ブラック M|表示名|
|category|string|apparel / book / cosmetics ...|カテゴリ|
|length_mm|int|260|三辺（mm）|
|width_mm|int|210|三辺（mm）|
|height_mm|int|25|三辺（mm）|
|weight_g|int|220|重量（g）|
|can_rotate|int(0/1)|1|回転（向き変更）可否|
|fragile|int(0/1)|0|割れ物/要注意|
|compressible|int(0/1)|1|圧縮可能（衣類など）※v1は参考情報として保持|
|hazmat|int(0/1)|0|危険物/特殊※v1は参考情報として保持|
|padding_mm|int|5|緩衝材・作業余白（片側mm）。計算では **各辺に2×padding** を加算|
|prohibited_group|string|food / chemical / battery ...|同梱禁止判定のためのグループ（空=制約なし）|

#### 5.1.2 boxes.csv（箱マスタ）
|列名|型|例|説明|
|---|---:|---|---|
|box_id|string|BX-80|箱ID（ユニーク）|
|name|string|ダンボール 80|表示名|
|inner_length_mm|int|300|内寸（mm）|
|inner_width_mm|int|220|内寸（mm）|
|inner_height_mm|int|180|内寸（mm）|
|max_weight_g|int|5000|最大積載重量|
|box_cost_yen|int|55|箱資材コスト|
|box_type|string|box / mailer / long|箱種別（送料計算や制約に使用）|
|outer_length_mm|int|310|外寸（mm）送料サイズ計算用|
|outer_width_mm|int|230|外寸（mm）送料サイズ計算用|
|outer_height_mm|int|190|外寸（mm）送料サイズ計算用|

#### 5.1.3 shipping_rates.csv（運賃表）
- デモは簡易化し、都道府県別ではなく「全国一律」扱いでOK

|列名|型|例|説明|
|---|---:|---|---|
|carrier|string|CarrierB|配送キャリア名（ダミー）|
|service|string|Economy / Standard / Mail|サービス名|
|size_class|string/int|60 / 80 / ... / MAIL|サイズ区分。MAILはメール便扱い|
|max_weight_g|int|20000|重量上限|
|price_yen|int|790|送料|

#### 5.1.4 orders.csv（受注）
|列名|型|例|説明|
|---|---:|---|---|
|order_id|string|ORD-0001|受注ID|
|order_date|date|2026-01-25|受注日|
|channel|string|自社EC|販売チャネル|
|destination_prefecture|string|東京都|配送先（デモ表示用）|
|status|string|created / picking / packing / shipped|簡易ステータス|
|customer_note|string|食品と洗剤を同梱しないでください|備考（表示のみ）|

#### 5.1.5 order_items.csv（受注明細）
|列名|型|例|説明|
|---|---:|---|---|
|order_id|string|ORD-0001|受注ID（FK）|
|sku_id|string|SKU-TSHIRT-BLK-M|SKU（FK）|
|qty|int|2|数量|

#### 5.1.6 prohibited_group_pairs.csv（同梱禁止）
|列名|型|例|説明|
|---|---:|---|---|
|group_a|string|food|グループA|
|group_b|string|chemical|グループB|
|reason|string|食品と洗剤などの薬剤は同梱不可|UIに出す理由|

---

## 6. 画面要件（UI）
### 6.1 グローバル
- ヘッダーにナビ：Dashboard / Orders / Packing Assistant / Simulator / Masters / Logs
- すべてPCブラウザ前提（レスポンシブは最低限でOK）

### 6.2 Dashboard
- 今日の未出荷件数、推奨採用率（直近7日）、同梱分割件数
- “差し替え理由 TOP5”（Logsから集計）
- “送料見積り合計（推奨ベース）”の概算表示

### 6.3 Orders（一覧）
- テーブル列：
  - order_id（リンク）
  - order_date / channel / destination_prefecture / status
  - 推奨：shipment数、推奨箱（代表）、見積送料合計
  - アクション：`提案を再計算` / `梱包開始`
- フィルタ：status、日付範囲、チャネル

### 6.4 Order Detail（受注詳細）
- 受注明細（SKU名、数量、寸法、重量、属性）
- **梱包提案（shipments）**をカード表示：
  - Shipment #、含まれるSKU
  - 推奨箱（箱内寸/外寸/最大重量）
  - 見積送料（carrier/service/size_class/金額）
  - 充填率（fill ratio）
  - 注意（同梱禁止で分割した場合は理由を表示）
- ボタン：
  - `この提案で梱包開始`（Packing Assistantへ）
  - `提案を再計算`

### 6.5 Packing Assistant（現場向け）
- 上部で `order_id` を入力（または一覧から遷移）
- 画面に大きく：
  - 推奨箱（箱名・サイズクラス・送料）
  - Shipmentが複数ある場合、タブで切替
- 手順エリア：
  - 商品リスト（数量含む）
  - 注意タグ（fragile / battery / liquid など）
- 実績入力：
  - 実際に使った箱（ドロップダウン）
  - 推奨と違う場合：理由（選択式 + 自由記述）
    - 例：入らない / 緩衝材が多い / 箱在庫切れ / 破損リスク / オペレーション都合
  - （任意）作業者名
  - `確定してログ保存` ボタン

### 6.6 Simulator（新商品 送料シミュレーション）
- 入力フォーム：
  - SKU名（任意）、カテゴリ、三辺（mm）、重量（g）、padding(mm)、fragile、can_rotate
- 出力：
  - 入る箱候補 上位5件（箱名、サイズクラス、送料、箱コスト、合計、充填率目安）
  - carrier/serviceの切替（CarrierA/CarrierB）
- ボタン：
  - `SKUとして登録`（入力値でSKUマスタに追加）

### 6.7 Masters（マスタ管理）
- SKUマスタ：一覧、編集、追加、CSVインポート、CSVエクスポート
- 箱マスタ：一覧、編集、追加、CSVインポート
- 運賃表：一覧、編集、CSVインポート
- 同梱禁止ルール：一覧、追加（groupペア）

### 6.8 Logs（ログ/簡易分析）
- 一覧：日時、order_id、shipment_no、推奨箱、実箱、理由、作業者
- フィルタ：期間、理由、SKU（オプション）
- 簡易集計：推奨採用率、差し替え理由TOP、入らない多発SKU

---

## 7. 梱包推薦ロジック要件（デモ用アルゴリズム v1）
### 7.1 前処理（寸法の扱い）
- SKUの実効寸法は以下で計算する（mm）  
  `effective = (length_mm + 2*padding_mm, width_mm + 2*padding_mm, height_mm + 2*padding_mm)`
- 数量（qty）は単位に展開して扱って良い（デモは数が少ない想定）

### 7.2 同梱禁止の分割
- `prohibited_group_pairs.csv` のペアに該当するグループが同一shipmentに混在しないよう、**自動でshipmentを分割**する。
- 実装は貪欲法でOK（決定性が重要）：
  1) 明細を item単位に展開
  2) `prohibited_group` でソート → 体積降順でソート
  3) 既存shipmentに入れても禁止ペアが発生しない最初のshipmentへ投入、入らなければ新規shipmentを作成

### 7.3 箱候補のフィルタ（fit判定）
箱候補ごとに以下を満たす必要がある：
- 重量：`sum(weight_g) <= box.max_weight_g`
- 各アイテムが単体で箱内寸に入る（回転可なら6通りの向きを許可）
- 体積充填率：  
  `fill_ratio = sum(item_volume) / box_volume`  
  - fragileが含まれる shipment は `fill_ratio <= 0.80`
  - それ以外は `fill_ratio <= 0.90`
  - box_type が `long` は `fill_ratio <= 0.90`（長尺はタイトでも良い）

> 注意：v1は厳密な3D配置ではなく「入る可能性が高い」判定に留める。  
> デモとしては十分。将来は3Dビンパッキングに差し替え可能な設計にする。

### 7.4 送料サイズの計算
- box_type が `mailer` の場合：`size_class = "MAIL"`
- それ以外：外寸からサイズクラスを決める  
  - `sum_cm = ceil(outer_length_mm/10)+ceil(outer_width_mm/10)+ceil(outer_height_mm/10)`
  - `sum_cm` が収まる最小の閾値を `size_class` とする（60/80/100/120/140/160）

### 7.5 運賃の参照（サービス選択ルール）
- `size_class == "MAIL"` → service は `Mail`
- それ以外 → service は `Economy`（デモでは最安系サービス扱い）
- 指定carrierに該当レートが無ければ CarrierA にフォールバックして良い

### 7.6 スコアリング（推奨箱の決定）
- デモv1では以下の合計コスト最小：
  - `total_cost = shipping_price + box_cost`
- 同額の場合のタイブレーク：
  1) 充填率が低い（余裕がある）ほう
  2) 箱体積が小さいほう

### 7.7 出力（画面に出す説明）
推薦結果には最低限以下を返す：
- recommended_box_id / box名
- carrier / service / size_class / shipping_yen
- box_cost_yen / total_cost_yen
- fill_ratio
- shipment分割理由（同梱禁止が原因なら reason を表示）

---

## 8. API要件（FastAPIのルーティング例）
> 実装はHTMLレンダリングでもJSONでも良いが、将来の外部連携を見据えJSONも併用する。

- `GET /` → Dashboard
- `GET /orders` → Orders一覧
- `GET /orders/{order_id}` → Order detail（提案も表示）
- `POST /orders/{order_id}/recalculate` → 提案再計算
- `GET /packing` → Packing Assistant（検索フォーム）
- `GET /packing/{order_id}` → Packing Assistant（shipment表示）
- `POST /packing/{order_id}/shipments/{no}/confirm` → 実績ログ保存
- `GET /simulator` → シミュレーター
- `POST /simulator` → シミュレーション実行
- `POST /simulator/register_sku` → SKU登録
- `GET /masters/skus` / `GET /masters/boxes` / `GET /masters/rates` / `GET /masters/prohibited`
- `POST /masters/*/import` → CSVインポート
- `GET /logs` → ログ一覧/集計
- `POST /admin/reset` → DB初期化（seed再投入）

---

## 9. DBスキーマ（SQLite）
### 9.1 マスタ
- `skus`
- `boxes`
- `shipping_rates`
- `prohibited_group_pairs`

### 9.2 トランザクション
- `orders`
- `order_items`

### 9.3 提案・実績・ログ
- `packing_plans`（orderごとに最新提案1件で良い）
- `packing_shipments`（1オーダーが複数shipmentに分割される）
- `packing_shipment_items`（shipment内のSKUと数量）
- `packing_execution_logs`（現場確定ログ。推奨と実績が一致/不一致も記録）

---

## 10. デモ用の“見せ場”シナリオ（seedデータに含む）
- `ORD-0001`：衣類＋小物 → **メール便（mailer）** が出る
- `ORD-0004`：ポスター → **長尺箱** が出る
- `ORD-0005`：食品＋洗剤 → **同梱不可で2箱に自動分割** が出る
- `ORD-0007`：ボードゲーム（大きい）→ **140箱** が出る

---

## 11. 受入条件（Acceptance Criteria）
1. seed投入後、Orders一覧に **60件**の受注が表示される
2. 任意の受注を開くと、shipmentが生成され推奨箱が表示される（失敗しない）
3. `ORD-0005` は shipment が **2つ**に分割され、理由（食品と薬剤の同梱不可）が表示される
4. Packing Assistantで推奨と異なる箱を選べ、理由を付けて保存でき、Logsに反映される
5. Simulatorで任意寸法を入力すると、箱候補が出て送料・合計が表示される
6. マスタのCSVインポートが動作し、再計算に反映される

---

## 12. Codexに渡す実装指示（そのまま貼れる形）
- 上記要件を満たす **FastAPI + SQLite + Jinja2** のWebアプリを実装すること
- `seed/` のCSVから初回投入する仕組みを実装すること
- 梱包推薦ロジック（セクション7）を `services/packing.py` 等に分離して実装すること
- 画面はBootstrap等を使って見栄えを整える（色指定不要）
- 例外が起きても画面が落ちないように、エラーメッセージをUIに表示すること

