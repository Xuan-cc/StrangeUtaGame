英単語カナ読み辞書
e2k.txt
---

The CMU Pronouncing Dictionary
カーネギーメロン大学が公開している発音辞書が大元となってます。
http://www.speech.cs.cmu.edu/cgi-bin/cmudict
https://github.com/Alexir/CMUdict

ライセンス： BSD-3-Clause License

-

英語をカタカナ表記に変換
モリカトロン宮本 様

Morikatron Engineer Blog
(英語をカタカナ表記に変換してみる)
https://tech.morikatron.ai/entry/2020/05/25/100000

<GitHub> english_to_kana (Pythonプログラム)
https://github.com/morikatron/snippet/tree/master/english_to_kana

・上記プログラムを使用してカナ読みに変換した辞書をe2k.txtとして同梱しています。
・以下のプログラムを追記して、「ッ」がある程度つくようにしています。
　（見つける度に追記しているがまだまだ調整不足。条件かなりバラバラだけどどこか合わせられそうかも？）

<english_to_kana.py>
                                ：
                                ：
                    elif s in self.vowels:
                        # 母音
                        # aiueoに割り振る
                        if s in {'AA', 'AH'}:
                                ：
                                ：
                                ：
                            elif s in {'AW'}:
                                yomi += 'ウ'
                   # ↑ - 原文 -
                   # ↓ - 追記 -
                        # ッ =====================================
                        # EH かつ 1つ後 T (末尾じゃなくてもよい)
                        if s in {'EH'} and s_next in {'T'}:
                            ###ただし、1つ前が B の場合は基本ダメ
                            if s_prev in {'B'}:
                                # ダメ...なんだけど、1つ後の T で終わる場合は ッ を足す
                                if i == len(sound_list)-3:
                                    yomi += 'ッ'
                                # 1つ後が T で、2つ後ろがあり S で終わる場合も ッ を足す
                                elif i == len(sound_list)-4 and sound_list[i+2] in {'S'}:
                                    yomi += 'ッ'
                            else:
                                yomi += 'ッ'
                        # UH かつ 1つ後 K,D,T
                        if (not yomi.endswith('ッ')) and s in {'UH'} and s_next in {'K','D','T'}:
                           yomi += 'ッ'
                        # AE
                        if (not yomi.endswith('ッ')) and s in {'AE'}:
                            # 1つ後 P(+条件)
                            if s_next in {'P'}:
                                # 1つ前 L,HH
                                if s_prev in {'L','HH'}:
                                    yomi += 'ッ'
                                # 1つ前 N,R かつ 2つ前 子音
                                elif s_prev in {'N','R'} and i > 1 and sound_list[i-2] in self.kana_dic:
                                    yomi += 'ッ'
                                # 1つ前 K,T かつ 1つ後ろのPで単語が終わる
                                elif s_prev in {'K','T'} and i == len(sound_list)-3:
                                    yomi += 'ッ'
                            # 1つ後 D(+条件)
                            if s_next in {'D'}:
                                # 優先事項（B R AE D は末尾じゃなくてもよい）
                                if s_prev in {'R'} and i > 1 and sound_list[i-2] in {"B"}:
                                    yomi += 'ッ'
                                # その他は 1つ前 子音(+もう１つ条件)
                                elif s_prev in self.kana_dic:
                                    # D で単語が終わる
                                    if i == len(sound_list)-3:
                                        yomi += 'ッ'
                                    # 2つ後ろがあり Z で終わる
                                    elif i == len(sound_list)-4 and sound_list[i+2] in {'Z'}:
                                        yomi += 'ッ'
                                    
                        # AH
                        if (not yomi.endswith('ッ')) and s in {'AH'}:
                            # 1つ前 L かつ 1つ後 K(+もう１つ条件)
                            if s_prev in {'L'} and s_next in {'K'}:
                                # K で単語が終わる
                                if i == len(sound_list)-3:
                                    yomi += 'ッ'
                                # 2つ後ろがあり IY,S で終わる
                                elif i == len(sound_list)-4 and sound_list[i+2] in {'IY','S'}:
                                    yomi += 'ッ'
                            # (1つ後)2つ後ろ3つ後ろ以上があり TH IH NG
                            elif i <= len(sound_list)-5 and s_next in {'TH'} and sound_list[i+2] in {'IH'} and sound_list[i+3] in {'NG'}:
                                yomi += 'ッ'
                                    
                        # SH-AA-K 基本OK
                        if (not yomi.endswith('ッ')) and s_prev in {'SH'} and s in {'AA'} and s_next in {'K'}:
                            #ただし、K の次があり、 AH UW の場合はダメ
                            if not (i <= len(sound_list)-4 and sound_list[i+2] in {'AH','UW'}):
                                    yomi += 'ッ'
                                    
                        # L-IH-SH」(L以外いけないのかどうかは未調査)
                        if (not yomi.endswith('ッ')) and s_prev in {'L'} and s in {'IH'} and s_next in {'SH'} and i == len(sound_list)-3:
                            yomi += 'ッ'
                                
                        # {特定の子音} P」P S」で終わる系譜（もしくは P IH NG）
                        # AA AH EH
                        if (not yomi.endswith('ッ')) and s in {'AA','AH','EH'}:
                            # 1つ後 P(+もう１つ条件)
                            if s_next in {'P'}:
                                # P で単語が終わる
                                if i == len(sound_list)-3:
                                    yomi += 'ッ'
                                # 2つ後ろがあり S で終わる
                                elif i == len(sound_list)-4 and sound_list[i+2] in {'S'}:
                                    yomi += 'ッ'
                                # 2つ後ろ3つ後ろ以上があり IH NG
                                elif i <= len(sound_list)-5 and sound_list[i+2] in {'IH'} and sound_list[i+3] in {'NG'}:
                                    yomi += 'ッ'
                                    
                        # {特定の子音} T」T S」で終わる系譜
                        # IH AE AH AA
                        if (not yomi.endswith('ッ')) and s in {'IH','AE','AH','AA'}:
                            # 1つ後 T(+もう１つ条件)
                            if s_next in {'T'}:
                                # T で単語が終わる
                                if i == len(sound_list)-3:
                                    yomi += 'ッ'
                                # 2つ後ろがあり S で終わる
                                elif i == len(sound_list)-4 and sound_list[i+2] in {'S'}:
                                    yomi += 'ッ'
                                    
                        # {特定の子音} K」K S」で終わる系譜（もしくは K IH NG）
                        # IH AE
                        if (not yomi.endswith('ッ')) and s in {'IH','AE'}:
                            # 1つ後 K(+もう１つ条件)
                            if s_next in {'K'}:
                                # K で単語が終わる
                                if i == len(sound_list)-3:
                                    yomi += 'ッ'
                                # 2つ後ろがあり S で終わる
                                elif i == len(sound_list)-4 and sound_list[i+2] in {'S'}:
                                    yomi += 'ッ'
                                # 2つ後ろ3つ後ろ以上があり IH NG
                                elif i <= len(sound_list)-5 and sound_list[i+2] in {'IH'} and sound_list[i+3] in {'NG'}:
                                    yomi += 'ッ'
                                    
                        # {特定の子音} K」K T」で終わる系譜（もしくは K IH NG）
                        # AA
                        if (not yomi.endswith('ッ')) and s in {'AA'}:
                            # 1つ後 K(+もう１つ条件)
                            if s_next in {'K'}:
                                # K で単語が終わる
                                if i == len(sound_list)-3:
                                    yomi += 'ッ'
                                # 2つ後ろがあり T で終わる
                                elif i == len(sound_list)-4 and sound_list[i+2] in {'T'}:
                                    yomi += 'ッ'
                                # 2つ後ろ3つ後ろ以上があり IH NG
                                elif i <= len(sound_list)-5 and sound_list[i+2] in {'IH'} and sound_list[i+3] in {'NG'}:
                                    yomi += 'ッ'

                        # {特定の子音} D」D Z」で終わる系譜
                        # EH
                        if (not yomi.endswith('ッ')) and s in {'EH'}:
                            # 1つ後 D(+もう１つ条件)
                            if s_next in {'D'}:
                                # D で単語が終わる
                                if i == len(sound_list)-3:
                                    yomi += 'ッ'
                                # 2つ後ろがあり Z で終わる
                                elif i == len(sound_list)-4 and sound_list[i+2] in {'Z'}:
                                    yomi += 'ッ'
                                    
                        # {特定の子音} CH」CH Z」で終わる系譜（もしくは CH IH NG）
                        # AA AH(CH」のみ存在)
                        if (not yomi.endswith('ッ')) and s in {'AA','AH'}:
                            # 1つ後 CHで終わる
                            if s_next in {'CH'}:
                                # CH で単語が終わる
                                if i == len(sound_list)-3:
                                    yomi += 'ッ'
                                # 2つ後ろがあり Z で終わる
                                elif i == len(sound_list)-4 and sound_list[i+2] in {'Z'}:
                                    yomi += 'ッ'
                                # 2つ後ろ3つ後ろ以上があり IH NG
                                elif i <= len(sound_list)-5 and sound_list[i+2] in {'IH'} and sound_list[i+3] in {'NG'}:
                                    yomi += 'ッ'

                         
                        # {特定の子音} JH」JH D」JH IH Z」で終わる系譜
                        # IH
                        if (not yomi.endswith('ッ')) and s in {'IH'}:
                            # 1つ後 JHで終わる
                            if s_next in {'JH'}:
                                # JH で単語が終わる
                                if i == len(sound_list)-3:
                                    yomi += 'ッ'
                                # 2つ後ろがあり D で終わる
                                elif i == len(sound_list)-4 and sound_list[i+2] in {'D'}:
                                    yomi += 'ッ'
                                # 2つ後ろと3つ後ろがあり IH-Z で終わる
                                elif i == len(sound_list)-5 and sound_list[i+2] in {'IH'} and sound_list[i+3] in {'Z'}:
                                    yomi += 'ッ'
                   # ↑ - 追記 -
                   # ↓ - 原文 -
                if log:
                    log_text += word + ' ' + yomi + ' ' + p + '\n'
                # 登録
                self.eng_kana_dic[word] = yomi
                                ：
                                ：