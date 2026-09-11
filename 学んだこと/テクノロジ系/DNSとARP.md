# DNSとARP

<!-- past-exam-sync: 過去問/令和6年度秋季解説.md#午前I-問11:start -->
## 2026-09-07

### DNSの正引き・逆引きと代表的なリソースレコード

- **DNS（Domain Name System）**は、ドメイン名・ホスト名とIPアドレスなどの情報を対応付ける仕組みである。
- 令和6年度秋期 高度試験 午前I 問11の正解は **ウ**。**IPアドレスから対応するホスト名を調べることを逆引き**という。
- 反対に、ホスト名からIPアドレスを調べることは**正引き**である。IPv4の正引きでは主に `A`、IPv6では `AAAA`、逆引きでは `PTR` レコードを使う。

| 選択肢 | 判断 | 正しい知識 |
| --- | --- | --- |
| ア DNSサーバーのホスト名を登録するのがMX | 誤り | **MX**はメール配送先のメールサーバーを指定する。権威DNSサーバーは**NS** |
| イ DNSサーバーへ問い合わせるソフトウェアをレゾリューションという | 誤り | 問い合わせを行うソフトウェア・機能は**リゾルバ（resolver）**。resolutionは名前解決という処理を指す |
| **ウ IPアドレスからホスト名を調べるのが逆引き** | **正しい** | 逆引きではPTRレコードを利用する |
| エ ホスト名の別名を登録するのがNS | 誤り | ホスト名の別名は**CNAME**。NSはそのゾーンを担当するネームサーバー |

| レコード | 主な意味 |
| --- | --- |
| A | ホスト名 → IPv4アドレス |
| AAAA | ホスト名 → IPv6アドレス |
| PTR | IPアドレス → ホスト名の逆引き |
| MX | メールを受け取るメールサーバー |
| NS | ゾーンを担当する権威DNSサーバーのホスト名 |
| CNAME | ホスト名の別名 |

#### Related To

- A1-0284（DNSの正引き・逆引きと主要レコード）
- A1-0364（対比：問い合わせ機能であるリゾルバと処理を指す名前解決）

#### 参考

- [IPA：令和6年度秋期 高度試験 午前Ⅰ 問題](https://www.ipa.go.jp/shiken/mondai-kaiotu/m42obm000000afqx-att/2024r06a_koudo_am1_qs.pdf)
- [IPA：令和6年度秋期 高度試験 午前Ⅰ 解答例](https://www.ipa.go.jp/shiken/mondai-kaiotu/m42obm000000afqx-att/2024r06a_koudo_am1_ans.pdf)
<!-- past-exam-sync: 過去問/令和6年度秋季解説.md#午前I-問11:end -->

<!-- past-exam-sync: 過去問/令和6年度秋季解説.md#午前I-問12:start -->
## 2026-09-07

### ARPとARPキャッシュを確認するコマンド

- **ARP（Address Resolution Protocol）**は、同一IPv4 LAN上で、通信相手又は次に渡す相手の**IPv4アドレスに対応するMACアドレスを求める**ための仕組みである。
- 令和6年度秋期 高度試験 午前I 問12では、プリンターのIPアドレスが分かっており、そのプリンターを直前に使用している。同一LANでの通信時に得たIPアドレスとMACアドレスの対応がARPキャッシュに残っている可能性が高いため、正解は **ア：`arp`**。

| コマンド | 主な用途 | 問12での判断 |
| --- | --- | --- |
| **arp** | ARPキャッシュのIPアドレスとMACアドレスの対応を表示・管理する | **正解** |
| ipconfig / ifconfig | 自分の端末のIPアドレスやインタフェース設定などを確認する | 相手プリンターのMAC確認が主目的ではない |
| netstat | 通信接続、待受けポート、経路情報などを確認する | IP-MAC対応表を確認するコマンドではない |
| ping | ICMP Echoで到達性を確認する | 通信によってARP解決を発生させることはあるが、MACアドレス一覧を表示するコマンドではない |

#### ARPの流れ

```text
PC: 「192.0.2.20を持っている端末、MACアドレスを教えて」
        ↓ ARP Request（同一LANへブロードキャスト）
Printer: 「私です。MACは aa:bb:cc:dd:ee:ff」
        ↓ ARP Reply
PC: IP ↔ MAC の対応をARPキャッシュへ一時保存
```

#### Related To

- A1-0186（MACアドレス・ARP・NDP）
- A1-0293（端末のネットワーク設定とARPキャッシュの確認）
- A1-0365（対比：arp・ipconfig／ifconfig・netstat・pingの用途）

#### 参考

- [IPA：令和6年度秋期 高度試験 午前Ⅰ 問題](https://www.ipa.go.jp/shiken/mondai-kaiotu/m42obm000000afqx-att/2024r06a_koudo_am1_qs.pdf)
- [IPA：令和6年度秋期 高度試験 午前Ⅰ 解答例](https://www.ipa.go.jp/shiken/mondai-kaiotu/m42obm000000afqx-att/2024r06a_koudo_am1_ans.pdf)
<!-- past-exam-sync: 過去問/令和6年度秋季解説.md#午前I-問12:end -->

## 2026-09-08

### CNとCNAMEの違い

| 用語 | 層・場所 | 意味 | 例 |
| --- | --- | --- | --- |
| **CN（Common Name）** | TLS証明書のSubject | 証明書に記載される名前 | `CN=example.com` |
| **CNAME（Canonical Name）** | DNSのリソースレコード | ホスト名の別名を正規名へ対応付けるレコード種別 | `www.example.com CNAME example.com` |

- 名前は似ているが、**CNは証明書の名前、CNAMEはDNSの別名レコード**であり、同じものではない。

#### Related To

- A1-0284（DNSの正引き・逆引きと主要レコード）
- A1-0298（CNとCNAMEの違い）
