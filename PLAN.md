# my-dictation 実装プラン

## 1. 目的

音声をGroqで文字起こしし、決定的な正規化、技術用語補正、保守的なLLM校正を順番に適用して、入力したかった自然な文章を生成する。

初期実装では複雑な自動学習やN-best処理を行わず、各段階のテキストを人間が確認できるJSONファイルとして保存する。

## 2. 初期スコープ

### 実装する

- GroqによるASR
- ASR失敗時の音声一時保存とリトライ
- 日本語ITN
  - 数字
  - 日付
  - 時刻
  - 金額
  - 単位
- Mondegreenによる固有名詞・技術用語補正
- OpenAI互換APIによる保守的なLLM校正
- Mondegreen修正語の保護
- 各処理段階の履歴保存
- 不満のある結果への手動修正記録
- 最小CLI

### 初期実装では扱わない

- N-best、lattice、候補rerank
- Adaptive GER
- ユーザー確定文の自動追跡
- 辞書の自動更新
- 音声の恒久保存
- 独立した検証モデル
- Mac用GUI・常駐音声入力アプリ

## 3. 処理パイプライン

```text
音声
 ↓
一時音声ファイルをspoolへ作成
 ↓
Groq ASR（1-best）
 ├─ 失敗: spoolへ残してリトライ可能にする
 └─ 成功: rawを記録して音声を削除
 ↓
ITN
数字・日付・時刻・金額・単位を限定的に正規化
 ↓
Mondegreen
辞書にある固有名詞・技術用語を音韻的に補正
 ↓
OpenAI互換LLM
フィラー・重複・言い直し・明白な誤認識を保守的に修正
 ↓
保護語の保持確認
 ↓
outputを保存・出力
```

### 処理原則

- すべての処理は1-best文字列だけで動作する。
- 決定的に直せるものを先に処理する。
- Mondegreenが確定した用語はLLMへ保護語として渡す。
- LLMが保護語を変更・削除した場合、その出力をそのまま採用しない。
- 意味の追加、要約、不要な言い換え、文体変更をLLMに許可しない。
- 各段階は入力と出力を履歴へ残す。

## 4. 構成要素

### 4.1 ASR

責務：

- 音声ファイルをGroqへ送信する
- 1-best文字列を返す
- provider固有レスポンスを内部へ閉じ込める
- 失敗理由を呼び出し側へ返す

初期providerはGroqとするが、校正処理はGroq固有型へ依存させない。

```text
transcribe(audio) -> AsrResult
```

### 4.2 音声spool

責務：

- 録音・受領した音声を送信前に一時保存する
- ASR成功後に削除する
- ASR失敗時は残す
- 残った音声を明示的に再試行できるようにする

想定構造：

```text
data/
└── spool/
    └── <timestamp>-<id>.<audio-extension>
```

プロセス異常終了時にも音声が失われないよう、ASR成功と記録保存の完了後に削除する。

### 4.3 ITN

責務：

- 数字、日付、時刻、金額、単位を決定的に正規化する
- 変更前後と適用規則を返す

第一候補はWeTextProcessing。平仮名数詞への適合や誤変換が問題になる場合は、対象spanを限定する前処理を追加する。

電話番号、住所、曖昧な助数詞は初期対象外とする。

### 4.4 Mondegreen

責務：

- 辞書から音韻的に近い候補を検索する
- 厳しい閾値で固有名詞・技術用語を置換する
- 修正した用語を保護spanとして返す

初期は小さな手動辞書とし、LM rerankerは使用しない。

### 4.5 LLM校正

責務：

- フィラーを削除する
- 明白な重複・言い直しを整理する
- 文脈上明らかなASR誤認識を修正する
- 必要最低限の句読点を付与する

OpenAI互換APIを使用し、特定vendorのSDKや独自レスポンスへ依存しない。

設定項目：

- base URL
- API key
- model
- timeout
- temperature等の生成設定

LLMへは本文と保護語を渡す。可能なら自由文ではなく、校正済み本文と変更一覧を構造化レスポンスとして要求する。

### 4.6 保護語確認

責務：

- Mondegreenが返した保護語がLLM出力に残っているか確認する
- 違反時に安全なfallbackを選ぶ

初期fallback：

1. LLM出力で保護語が維持されていれば採用
2. 違反していればLLM段階を不採用
3. Mondegreen段階のテキストをoutputとして使用

### 4.7 履歴保存

1入力につき1つのJSONファイルを作る。

```text
data/
└── records/
    └── YYYY-MM-DD/
        └── HHMMSS-<id>.json
```

初期schema案：

```json
{
  "id": "uuid",
  "created_at": "ISO-8601",
  "asr": {
    "provider": "groq",
    "model": "whisper-large-v3-turbo",
    "raw": "ASRの1-best文字列"
  },
  "stages": [
    {
      "name": "itn",
      "processor": "wetextprocessing",
      "input": "...",
      "output": "...",
      "changes": []
    },
    {
      "name": "terminology",
      "processor": "mondegreen",
      "input": "...",
      "output": "...",
      "changes": [],
      "protected_terms": []
    },
    {
      "name": "llm",
      "processor": "openai-compatible",
      "model": "...",
      "input": "...",
      "output": "...",
      "changes": [],
      "accepted": true
    }
  ],
  "output": "最終出力",
  "manual_correction": null
}
```

悪い結果だけ、ユーザーが`manual_correction`へ修正文を手動記録する。

保存は一時ファイルへ書いた後にrenameし、途中終了で壊れたJSONを残さない。

## 5. CLI

初期コマンド案：

```text
my-dictation transcribe <audio-file>
my-dictation retry [record-or-audio-id]
my-dictation process-text <text>
```

- `transcribe`: ASRから全校正パイプラインを実行
- `retry`: spoolに残った失敗音声を再送
- `process-text`: ASRを通さず校正部分だけ試験

標準出力には最終テキストだけを出し、ログや履歴パスは標準エラーまたはオプション出力へ分離する。

## 6. 実装順

### フェーズ1：骨格と記録

1. 使用言語・パッケージ構成を確定する
2. 設定読み込みを作る
3. pipelineの共通インターフェースを作る
4. 1件1JSONのschemaとatomic writeを実装する
5. `process-text` CLIを作る

完了条件：ダミー処理を通した各段階がJSONへ保存される。

### フェーズ2：Groq ASRとspool

1. 音声spoolを実装する
2. Groq adapterを実装する
3. 成功時削除、失敗時保持を実装する
4. `transcribe`と`retry`を実装する

完了条件：成功音声は削除され、失敗音声は再試行でき、rawが記録される。

### フェーズ3：ITN

1. WeTextProcessingを隔離して接続する
2. 対象カテゴリを限定する
3. 入出力と変更内容を履歴へ残す
4. 平仮名数詞と固有名詞破壊を手元の例で確認する

完了条件：対象カテゴリだけが再現可能に変換される。

### フェーズ4：Mondegreen

1. 小さな辞書形式を決める
2. LMなしでMondegreenを接続する
3. 厳しい閾値を設定する
4. 修正内容と保護語を返す

完了条件：登録した技術用語を局所修正し、一般語を不用意に変更しない。

### フェーズ5：LLM校正

1. OpenAI互換clientを実装する
2. 保守的校正promptと構造化出力を定義する
3. 保護語を入力へ含める
4. 保護語確認とfallbackを実装する
5. timeout・API失敗時はMondegreen段階へfallbackする

完了条件：LLMが失敗・違反しても、それ以前の安全なテキストを返せる。

### フェーズ6：実利用による調整

1. 実際に使用する
2. 悪い結果だけ`manual_correction`へ記録する
3. どの段階で悪化したか履歴から確認する
4. ITN規則、辞書、prompt、閾値を調整する

自動辞書更新や専用モデル学習は、手動修正が十分に蓄積してから検討する。

## 7. 最低限の確認項目

- ASR失敗時に音声が残る
- ASR成功後、記録保存前に音声を削除しない
- 記録成功後に音声が削除される
- 各段階の入力・出力がJSONに残る
- JSON書き込みがatomicである
- ITNが対象外の文字列を不用意に変更しない
- Mondegreenが辞書外の一般語を不用意に変更しない
- LLMが保護語を変更した場合にfallbackする
- LLM API失敗時にも前段テキストを返す
- API keyや音声を履歴JSONへ保存しない

## 8. 実装後に再検討する事項

- ASR providerの追加
- 日本語ITNの対象拡張
- gector-ja等のedit-basedモデル
- LLM校正の複数pass化
- 辞書更新支援
- 履歴検索が必要になった場合のSQLite移行
- Mac常駐クライアント
- N-bestは1-best経路の限界と改善効果が実証された場合のみ再検討する
