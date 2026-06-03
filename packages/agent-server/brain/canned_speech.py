"""Multilingual canned-speech strings for failure paths.

These strings are spoken by the agent on emergency code paths where we
can't rely on the LLM to phrase its own apology — typically because the
LLM itself is the failing component (network error, parse failure, retry
ceiling, batch ceiling). We pre-translate them into every language the
widget supports (the hardcoded list in
``packages/widget/src/Widget.tsx``), so a Spanish-speaking visitor
doesn't suddenly hear an English apology when something breaks.

Unknown language codes fall back to English.
"""

from __future__ import annotations

from enum import Enum


class CannedKey(str, Enum):
    """Stable identifiers for each canned utterance."""

    # Single LLM round failed (network/parse/timeout). Apology + invitation
    # to try again. Used both for non-tool-result inferences and for the
    # first 1-2 tool-result inference retries.
    LLM_GENERIC_ERROR = "llm_generic_error"

    # Tool-result inference has failed N times in a row for the same
    # in-flight batch. We're giving up on the demonstration to avoid an
    # infinite cost-burning loop. Spoken right before the active
    # demonstration is force-ended.
    INFERENCE_RETRY_EXHAUSTED = "inference_retry_exhausted"

    # The active demonstration has dispatched the maximum allowed number
    # of tool batches. We're force-ending it before the LLM tries another
    # batch under the same demo. Spoken right before the demonstration
    # is cleared.
    BATCH_CEILING_HIT = "batch_ceiling_hit"

    # Response-timeout recovery — the LLM accepted work but didn't push
    # a response within REPLY_WATCHDOG_SECONDS. We interrupt and
    # speak this directly (no LLM round) so the visitor isn't left
    # hanging in dead air.
    RESPONSE_TIMEOUT = "response_timeout"

    # Idle session goodbye — visitor went silent past the warning
    # window, agent is force-ending the session. Spoken once just
    # before the bot tears itself down.
    SESSION_IDLE_GOODBYE = "session_idle_goodbye"

    # 3-minute idle warning — fixed "are you still there?" check-in
    # spoken before stage 2 (the 60-second grace timer) starts. Was
    # an LLM-driven SYSTEM wake; now canned because the text is
    # deterministic and inference is wasted compute for fixed copy.
    IDLE_WARNING = "idle_warning"

    # 80-minute "10 minutes until cap" heads-up. Sessions hard-close
    # at 90 minutes (widget-side timer + server-side wait_for); this
    # canned warning fires at 80 min so the visitor has a chance to
    # wrap anything up before disconnect. Doesn't gate on activity —
    # fires once per session regardless of idle/active state.
    SESSION_CAP_WARNING = "session_cap_warning"


# Keys are ISO language codes matching the frontend's SUPPORTED_LANGUAGES.
# Currently covers 23 of aelios-spark's 37 widget languages — the remaining 14
# (bg, hr, el, gu, he, hu, kn, mr, no, ro, sk, tl, ta, te) fall back to
# English via the lookup at the bottom of this file. Translations are
# idiomatic, not literal — short apologies in each language's natural
# register.

_TRANSLATIONS: dict[CannedKey, dict[str, str]] = {
    CannedKey.LLM_GENERIC_ERROR: {
        "en": "Sorry — I hit a problem on my end. Could you try that again?",
        "es": "Disculpa, tuve un problema. ¿Podrías intentarlo de nuevo?",
        "fr": "Désolé, j'ai eu un souci de mon côté. Pouvez-vous réessayer ?",
        "de": "Entschuldigung, bei mir ist etwas schiefgelaufen. Könntest du es noch einmal versuchen?",
        "it": "Scusa, ho avuto un problema. Potresti riprovare?",
        "pt": "Desculpe, tive um problema aqui. Pode tentar de novo?",
        "nl": "Sorry, er ging iets mis aan mijn kant. Kun je het nog eens proberen?",
        "ru": "Извините, у меня что-то пошло не так. Попробуйте ещё раз, пожалуйста.",
        "ja": "すみません、こちらで問題が起きました。もう一度お願いできますか？",
        "ko": "죄송해요, 문제가 생겼어요. 다시 한 번 말씀해 주시겠어요?",
        "hi": "माफ़ कीजिए, मेरी तरफ से कुछ गड़बड़ हुई। क्या आप दोबारा कोशिश करेंगे?",
        "vi": "Xin lỗi, mình vừa gặp sự cố. Bạn thử lại giúp mình nhé?",
        "id": "Maaf, ada masalah di sisi saya. Bisa coba lagi?",
        "tr": "Özür dilerim, bir sorun yaşadım. Tekrar dener misiniz?",
        "pl": "Przepraszam, coś poszło nie tak. Czy możesz spróbować jeszcze raz?",
        "sv": "Förlåt, något gick fel hos mig. Kan du försöka igen?",
        "da": "Beklager, der gik noget galt hos mig. Kan du prøve igen?",
        "fi": "Anteeksi, jokin meni vikaan. Voisitko yrittää uudelleen?",
        "cs": "Promiňte, něco se pokazilo. Mohl(a) byste to zkusit znovu?",
        "ar": "آسف، حدثت مشكلة لدي. هل يمكنك المحاولة مرة أخرى؟",
        "ms": "Maaf, ada masalah di pihak saya. Boleh cuba sekali lagi?",
        "zh": "抱歉,我这边出了点问题。可以再试一次吗?",
        "th": "ขอโทษค่ะ มีปัญหาเกิดขึ้นทางฝั่งฉัน ลองอีกครั้งได้ไหมคะ?",
    },
    CannedKey.INFERENCE_RETRY_EXHAUSTED: {
        "en": "Something keeps going wrong on my end — I'll stop this walkthrough for now. You can try again, or ask me to show you something else.",
        "es": "Algo sigue fallando aquí — voy a detener esta demostración por ahora. Puedes volver a intentarlo o pedirme que te muestre otra cosa.",
        "fr": "Quelque chose continue de mal se passer de mon côté — je vais arrêter cette démonstration pour l'instant. Vous pouvez réessayer ou me demander de vous montrer autre chose.",
        "de": "Bei mir geht andauernd etwas schief — ich beende diese Vorführung erstmal. Du kannst es noch einmal versuchen oder mich nach etwas anderem fragen.",
        "it": "Qualcosa continua ad andare storto qui — interrompo la dimostrazione per ora. Puoi riprovare o chiedermi di mostrarti qualcos'altro.",
        "pt": "Algo continua dando errado do meu lado — vou parar essa demonstração por enquanto. Você pode tentar de novo ou me pedir para mostrar outra coisa.",
        "nl": "Er blijft iets misgaan aan mijn kant — ik stop deze rondleiding voor nu. Je kunt het opnieuw proberen of me vragen iets anders te laten zien.",
        "ru": "У меня всё время что-то идёт не так — я пока остановлю эту демонстрацию. Можете попробовать ещё раз или попросить показать что-то другое.",
        "ja": "こちらで問題が続いているので、いったんこのデモを止めますね。もう一度試していただくか、別のものをご案内しましょうか?",
        "ko": "제 쪽에서 계속 문제가 생기네요. 이 데모는 일단 중단할게요. 다시 시도하시거나 다른 것을 보여드릴 수도 있어요.",
        "hi": "मेरी तरफ से बार-बार कुछ गड़बड़ हो रही है — फ़िलहाल मैं यह वॉकथ्रू रोक रहा हूँ। आप फिर से कोशिश कर सकते हैं या कुछ और दिखाने को कह सकते हैं।",
        "vi": "Mình cứ gặp lỗi liên tục — mình tạm dừng phần hướng dẫn này nhé. Bạn có thể thử lại hoặc nhờ mình chỉ thứ khác.",
        "id": "Sepertinya terus ada masalah di sisi saya — saya hentikan dulu panduannya. Silakan coba lagi atau minta saya tunjukkan hal lain.",
        "tr": "Burada sürekli bir şeyler ters gidiyor — bu adımı şimdilik durduracağım. Tekrar deneyebilir ya da başka bir şey göstermemi isteyebilirsiniz.",
        "pl": "U mnie ciągle coś się psuje — przerwę na razie ten przewodnik. Możesz spróbować jeszcze raz lub poprosić mnie, żebym pokazał coś innego.",
        "sv": "Något fortsätter gå fel hos mig — jag avbryter den här genomgången tills vidare. Du kan försöka igen eller be mig visa något annat.",
        "da": "Der bliver ved med at gå noget galt hos mig — jeg stopper denne gennemgang for nu. Du kan prøve igen eller bede mig vise dig noget andet.",
        "fi": "Minulla menee jatkuvasti jokin pieleen — keskeytän tämän esittelyn toistaiseksi. Voit yrittää uudelleen tai pyytää näyttämään jotain muuta.",
        "cs": "Něco se mi pořád kazí — pro tuto chvíli toto provádění zastavím. Můžete to zkusit znovu nebo mě poprosit, ať vám ukážu něco jiného.",
        "ar": "يبدو أن هناك مشكلة متكررة لدي — سأتوقف عن هذا العرض الآن. يمكنك المحاولة مرة أخرى أو طلب أن أعرض لك شيئًا آخر.",
        "ms": "Sepertinya ada masalah yang berulang di pihak saya — saya akan hentikan demo ini buat masa ini. Anda boleh cuba lagi atau minta saya tunjuk perkara lain.",
        "zh": "我这边一直出问题,这次的演示我就先停下来了。你可以再试一次,或者让我给你看点别的。",
        "th": "ฉันยังเจอปัญหาเรื่อย ๆ — ขอหยุดการแนะนำนี้ไว้ก่อนนะคะ คุณลองใหม่อีกครั้งหรือบอกให้ฉันแสดงอย่างอื่นก็ได้ค่ะ",
    },
    CannedKey.RESPONSE_TIMEOUT: {
        "en": "We are sorry, the latest response took longer than expected. There might have been an error.",
        "es": "Lo sentimos, la última respuesta tardó más de lo esperado. Es posible que haya ocurrido un error.",
        "fr": "Nous sommes désolés, la dernière réponse a pris plus de temps que prévu. Il a peut-être eu une erreur.",
        "de": "Es tut uns leid, die letzte Antwort hat länger gedauert als erwartet. Möglicherweise ist ein Fehler aufgetreten.",
        "it": "Ci dispiace, l'ultima risposta ha richiesto più tempo del previsto. Potrebbe essersi verificato un errore.",
        "pt": "Lamentamos, a última resposta demorou mais do que o esperado. Pode ter ocorrido um erro.",
        "nl": "Het spijt ons, de laatste reactie duurde langer dan verwacht. Mogelijk is er een fout opgetreden.",
        "ru": "Извините, последний ответ занял больше времени, чем ожидалось. Возможно, произошла ошибка.",
        "ja": "申し訳ありません。最新の応答に予想より時間がかかりました。エラーが発生した可能性があります。",
        "ko": "죄송합니다. 최근 응답이 예상보다 오래 걸렸습니다. 오류가 발생했을 수 있습니다.",
        "hi": "हमें खेद है, नवीनतम प्रतिक्रिया अपेक्षा से अधिक समय ले रही है। कोई त्रुटि हो सकती है।",
        "vi": "Chúng tôi xin lỗi, phản hồi gần nhất mất nhiều thời gian hơn dự kiến. Có thể đã xảy ra lỗi.",
        "id": "Maaf, respons terbaru memerlukan waktu lebih lama dari yang diharapkan. Mungkin terjadi kesalahan.",
        "tr": "Üzgünüz, en son yanıt beklenenden uzun sürdü. Bir hata oluşmuş olabilir.",
        "pl": "Przepraszamy, najnowsza odpowiedź zajęła więcej czasu niż oczekiwano. Mógł wystąpić błąd.",
        "sv": "Vi ber om ursäkt, det senaste svaret tog längre tid än väntat. Det kan ha uppstått ett fel.",
        "da": "Vi beklager, det seneste svar tog længere tid end forventet. Der kan være opstået en fejl.",
        "fi": "Pahoittelemme, viimeisin vastaus kesti odotettua kauemmin. Saattoi tapahtua virhe.",
        "cs": "Omlouváme se, poslední odpověď trvala déle, než se očekávalo. Mohlo dojít k chybě.",
        "ar": "نعتذر، استغرقت آخر استجابة وقتًا أطول من المتوقع. قد يكون هناك خطأ.",
        "ms": "Kami minta maaf, respons terkini mengambil masa lebih lama daripada yang dijangkakan. Mungkin telah berlaku ralat.",
        "zh": "抱歉,最近的响应比预期花费的时间更长,可能发生了错误。",
        "th": "ขออภัยค่ะ การตอบกลับล่าสุดใช้เวลานานกว่าที่คาดไว้ อาจเกิดข้อผิดพลาดขึ้น",
    },
    CannedKey.BATCH_CEILING_HIT: {
        "en": "I've reached the limit of how much I can do in one walkthrough, so I have to end this demonstration here. You can ask me to try it again or move on to something else.",
        "es": "He alcanzado el límite de lo que puedo hacer en una demostración, así que tengo que terminarla aquí. Puedes pedirme que lo intente de nuevo o pasar a otra cosa.",
        "fr": "J'ai atteint la limite de ce que je peux faire en une seule démonstration, donc je dois l'arrêter ici. Vous pouvez me demander de réessayer ou de passer à autre chose.",
        "de": "Ich habe das Limit erreicht, was ich in einer Vorführung tun kann, deshalb muss ich sie hier beenden. Du kannst mich bitten, es noch einmal zu versuchen, oder etwas anderes ansehen.",
        "it": "Ho raggiunto il limite di quanto posso fare in una dimostrazione, quindi devo concluderla qui. Puoi chiedermi di riprovare o passare a qualcos'altro.",
        "pt": "Cheguei ao limite do que consigo fazer numa demonstração, então tenho de encerrá-la aqui. Pode me pedir para tentar de novo ou passar a outra coisa.",
        "nl": "Ik heb de limiet bereikt voor wat ik in één rondleiding kan doen, dus ik moet deze demonstratie hier beëindigen. Je kunt me vragen het opnieuw te proberen of iets anders te bekijken.",
        "ru": "Я достиг предела того, что могу сделать за одну демонстрацию, поэтому вынужден её здесь завершить. Вы можете попросить меня попробовать ещё раз или перейти к чему-то другому.",
        "ja": "1回のデモでできる上限に達してしまったので、ここで終了させていただきます。もう一度試していただくか、別のものをご案内することもできます。",
        "ko": "한 번의 데모에서 할 수 있는 한도에 도달해서 여기서 마무리할 수밖에 없네요. 다시 시도하시거나 다른 것을 보여드릴 수도 있어요.",
        "hi": "एक वॉकथ्रू में मैं जितना कर सकता हूँ, उसकी सीमा तक पहुँच गया हूँ, इसलिए यह डेमॉन्स्ट्रेशन यहीं ख़त्म करना पड़ेगा। आप मुझसे फिर से कोशिश करने या कुछ और देखने के लिए कह सकते हैं।",
        "vi": "Mình đã đạt đến giới hạn cho một phần hướng dẫn rồi nên đành phải dừng buổi giới thiệu ở đây. Bạn có thể nhờ mình thử lại hoặc chuyển sang việc khác.",
        "id": "Saya sudah mencapai batas untuk satu sesi panduan, jadi saya harus mengakhiri demonstrasi ini di sini. Silakan minta saya coba lagi atau beralih ke hal lain.",
        "tr": "Bir tanıtım için yapabileceğim sınıra ulaştım, bu yüzden bu demoyu burada bitirmem gerekiyor. Tekrar denememi isteyebilir ya da başka bir şeye geçebilirsiniz.",
        "pl": "Osiągnąłem limit tego, co mogę zrobić w jednym przewodniku, więc muszę zakończyć tę demonstrację w tym miejscu. Możesz poprosić mnie, żebym spróbował jeszcze raz, albo przejść do czegoś innego.",
        "sv": "Jag har nått gränsen för vad jag kan göra i en genomgång, så jag måste avsluta den här demonstrationen här. Du kan be mig försöka igen eller gå vidare till något annat.",
        "da": "Jeg har nået grænsen for, hvad jeg kan nå i én gennemgang, så jeg er nødt til at afslutte demonstrationen her. Du kan bede mig prøve igen eller gå videre til noget andet.",
        "fi": "Olen saavuttanut rajan sille, kuinka paljon voin tehdä yhden esittelyn aikana, joten minun on lopetettava tämä demonstraatio tähän. Voit pyytää minua yrittämään uudelleen tai siirtyä johonkin muuhun.",
        "cs": "Dosáhl jsem limitu toho, co dokážu během jednoho provádění, takže tuto ukázku tady musím ukončit. Můžete mě poprosit, ať to zkusím znovu, nebo přejít k něčemu jinému.",
        "ar": "لقد وصلت إلى الحد الأقصى لما يمكنني فعله في عرض واحد، لذا عليّ إنهاء هذا العرض هنا. يمكنك أن تطلب مني المحاولة مرة أخرى أو الانتقال إلى شيء آخر.",
        "ms": "Saya telah mencapai had untuk satu sesi panduan, jadi saya terpaksa menamatkan demonstrasi ini di sini. Anda boleh minta saya cuba lagi atau beralih ke perkara lain.",
        "zh": "我已经达到了一次演示的上限,所以这次的演示必须在这里结束。你可以让我再试一次,或者去看看别的内容。",
        "th": "ฉันถึงขีดจำกัดของสิ่งที่ทำได้ในการสาธิตครั้งเดียวแล้ว จึงต้องจบการสาธิตนี้ตรงนี้ค่ะ คุณสามารถให้ฉันลองอีกครั้งหรือไปดูเรื่องอื่นก็ได้ค่ะ",
    },
    CannedKey.IDLE_WARNING: {
        "en": "If there's nothing else you need help with, I'll close the session in about a minute. Just let me know otherwise.",
        "es": "Si no necesitas nada más, cerraré la sesión en aproximadamente un minuto. Avísame si necesitas algo.",
        "fr": "Si vous n'avez besoin de rien d'autre, je fermerai la session dans environ une minute. Faites-moi signe sinon.",
        "de": "Wenn du sonst nichts mehr brauchst, schließe ich die Sitzung in etwa einer Minute. Sag einfach Bescheid, falls doch.",
        "it": "Se non ti serve altro, chiuderò la sessione tra circa un minuto. Fammi sapere se hai bisogno di qualcosa.",
        "pt": "Se não precisar de mais nada, vou fechar a sessão em cerca de um minuto. Me avise caso precise de algo.",
        "nl": "Als je verder niets nodig hebt, sluit ik de sessie over ongeveer een minuut. Laat het me anders even weten.",
        "ru": "Если вам больше ничего не нужно, я закрою сессию примерно через минуту. Дайте знать, если что-то понадобится.",
        "ja": "他にご用事がなければ、約1分でセッションを終了します。何かあればお気軽にお知らせください。",
        "ko": "더 도와드릴 게 없으시면 약 1분 뒤에 세션을 닫을게요. 필요하시면 언제든 말씀해 주세요.",
        "hi": "अगर आपको और कुछ नहीं चाहिए, तो मैं लगभग एक मिनट में सत्र बंद कर दूँगा। ज़रूरत हो तो बताइए।",
        "vi": "Nếu bạn không cần gì thêm, mình sẽ đóng phiên sau khoảng một phút. Cứ nói cho mình biết nếu bạn cần gì nhé.",
        "id": "Kalau tidak ada lagi yang dibutuhkan, saya akan menutup sesi dalam sekitar satu menit. Kabari saja kalau perlu sesuatu.",
        "tr": "Başka bir şeye ihtiyacın yoksa yaklaşık bir dakika sonra oturumu kapatacağım. İhtiyacın olursa söylemen yeterli.",
        "pl": "Jeśli nie potrzebujesz niczego więcej, zamknę sesję za około minutę. Daj znać, gdybyś czegoś potrzebował.",
        "sv": "Om du inte behöver något mer stänger jag sessionen om ungefär en minut. Säg bara till om du behöver hjälp.",
        "da": "Hvis du ikke har brug for mere, lukker jeg sessionen om cirka et minut. Sig bare til, hvis du har brug for hjælp.",
        "fi": "Jos et tarvitse muuta apua, suljen istunnon noin minuutin kuluttua. Kerro vain, jos tarvitset jotain.",
        "cs": "Pokud nepotřebuješ nic dalšího, zavřu relaci asi za minutu. Stačí dát vědět, kdybys něco potřeboval.",
        "ar": "إذا لم تكن بحاجة إلى شيء آخر، فسأغلق الجلسة بعد دقيقة تقريبًا. فقط أخبرني إذا احتجت لأي شيء.",
        "ms": "Kalau tidak perlukan apa-apa lagi, saya akan menutup sesi dalam kira-kira seminit. Beritahu saja kalau perlukan bantuan.",
        "zh": "如果没有其他需要,我会在大约一分钟后关闭会话。需要的话随时告诉我。",
        "th": "ถ้าไม่มีอะไรให้ช่วยเพิ่มแล้ว ฉันจะปิดเซสชันในประมาณหนึ่งนาทีค่ะ มีอะไรเพิ่มเติมก็แจ้งได้เลยนะคะ",
    },
    CannedKey.SESSION_IDLE_GOODBYE: {
        "en": "I haven't heard anything for a while, so I'll close the session. Open me again any time you'd like more help.",
        "es": "No he escuchado nada por un rato, así que voy a cerrar la sesión. Vuélveme a abrir cuando quieras más ayuda.",
        "fr": "Je n'ai rien entendu pendant un moment, donc je vais fermer la session. Rouvrez-moi quand vous souhaitez plus d'aide.",
        "de": "Ich habe eine Weile nichts gehört, also schließe ich die Sitzung. Öffne mich wieder, wenn du Hilfe brauchst.",
        "it": "Non ho sentito nulla per un po', quindi chiudo la sessione. Riaprimi pure quando ti serve altro aiuto.",
        "pt": "Não ouvi nada por um tempo, então vou encerrar a sessão. Abra-me de novo quando precisar de mais ajuda.",
        "nl": "Ik heb een tijdje niets gehoord, dus ik sluit de sessie. Open me weer als je hulp nodig hebt.",
        "ru": "Я давно ничего не слышу, поэтому закрою сессию. Откройте меня снова, когда понадобится помощь.",
        "ja": "しばらく反応がなかったので、セッションを終了します。またいつでもお気軽に呼んでください。",
        "ko": "한동안 응답이 없어서 세션을 종료할게요. 도움이 더 필요하시면 언제든 다시 열어 주세요.",
        "hi": "काफी देर से कुछ सुनाई नहीं दिया, इसलिए मैं सत्र बंद कर रहा हूँ। जब भी मदद चाहिए, मुझे फिर से खोलें।",
        "vi": "Một lúc rồi mình không nghe gì, nên mình sẽ đóng phiên này lại. Bạn cứ mở lại khi cần hỗ trợ nhé.",
        "id": "Sudah cukup lama tidak ada respons, jadi saya akan menutup sesi ini. Buka saya lagi kapan saja jika butuh bantuan.",
        "tr": "Bir süredir ses yok, o yüzden oturumu kapatıyorum. Yardıma ihtiyacın olduğunda tekrar açabilirsin.",
        "pl": "Od jakiegoś czasu nic nie słyszę, więc zamknę sesję. Otwórz mnie ponownie, gdy będziesz potrzebować pomocy.",
        "sv": "Jag har inte hört något på ett tag, så jag stänger sessionen. Öppna mig igen när du vill ha hjälp.",
        "da": "Jeg har ikke hørt noget et stykke tid, så jeg lukker sessionen. Åbn mig igen, når du vil have hjælp.",
        "fi": "En ole kuullut mitään hetkeen, joten lopetan istunnon. Avaa minut milloin tahansa kun tarvitset apua.",
        "cs": "Chvíli jsem nic neslyšel, takže ukončuji relaci. Klidně mě otevřete znovu, kdykoli budete potřebovat pomoc.",
        "ar": "لم أسمع شيئاً منذ فترة، لذا سأنهي الجلسة. افتحني مرة أخرى متى احتجت إلى المساعدة.",
        "ms": "Sudah agak lama tiada respons, jadi saya akan tamatkan sesi ini. Buka saya semula bila-bila masa anda perlukan bantuan.",
        "zh": "我有一会儿没听到回应了,所以我先关闭这次会话。你随时再打开我寻求帮助。",
        "th": "ฉันไม่ได้ยินอะไรมาสักพักแล้ว เลยจะปิดเซสชันนี้นะคะ เปิดฉันอีกครั้งเมื่อใดก็ได้ที่ต้องการความช่วยเหลือ",
    },
    CannedKey.SESSION_CAP_WARNING: {
        "en": "Just a heads up — this session will close in about ten minutes. Let me know if there's anything else I can help with before then.",
        "es": "Solo un aviso — esta sesión se cerrará en aproximadamente diez minutos. Avísame si hay algo más en lo que pueda ayudarte antes.",
        "fr": "Juste pour info — cette session se fermera dans environ dix minutes. Faites-moi signe s'il y a autre chose que je peux faire avant.",
        "de": "Nur ein kurzer Hinweis — diese Sitzung schließt in etwa zehn Minuten. Sag Bescheid, falls ich vorher noch etwas erledigen soll.",
        "it": "Solo un avviso — questa sessione si chiuderà tra circa dieci minuti. Fammi sapere se c'è altro su cui posso aiutarti prima.",
        "pt": "Só para avisar — esta sessão será encerrada em cerca de dez minutos. Me avise se houver mais alguma coisa em que eu possa ajudar antes disso.",
        "nl": "Even een berichtje — deze sessie sluit over ongeveer tien minuten. Laat het me weten als er nog iets is waar ik mee kan helpen.",
        "ru": "Просто предупреждение — эта сессия закроется примерно через десять минут. Дайте знать, если я могу помочь с чем-то ещё до этого.",
        "ja": "お知らせです — このセッションは約10分後に終了します。それまでに他にお手伝いできることがあればお知らせください。",
        "ko": "잠깐 안내드릴게요 — 이 세션은 약 10분 뒤에 종료됩니다. 그 전에 더 도와드릴 일이 있으면 말씀해 주세요.",
        "hi": "बस एक सूचना — यह सत्र लगभग दस मिनट में बंद हो जाएगा। उससे पहले अगर कुछ और मदद चाहिए तो बताइए।",
        "vi": "Chỉ là một thông báo nhỏ — phiên này sẽ đóng trong khoảng mười phút nữa. Cứ cho mình biết nếu bạn cần hỗ trợ thêm gì trước đó nhé.",
        "id": "Sekadar info — sesi ini akan ditutup sekitar sepuluh menit lagi. Beritahu saja kalau ada yang masih perlu dibantu sebelum itu.",
        "tr": "Hızlı bir hatırlatma — bu oturum yaklaşık on dakika sonra kapanacak. Ondan önce yardımcı olabileceğim başka bir şey varsa söylemen yeterli.",
        "pl": "Krótka informacja — ta sesja zostanie zamknięta za około dziesięć minut. Daj znać, jeśli mogę w czymś jeszcze pomóc wcześniej.",
        "sv": "Bara en notis — den här sessionen stängs om ungefär tio minuter. Säg till om det är något mer jag kan hjälpa med innan dess.",
        "da": "Lige en information — denne session lukker om cirka ti minutter. Sig til, hvis der er noget mere, jeg kan hjælpe med før det.",
        "fi": "Pieni huomautus — tämä istunto sulkeutuu noin kymmenen minuutin kuluttua. Kerro, jos voin auttaa vielä jossain ennen sitä.",
        "cs": "Jen informace — tato relace se uzavře asi za deset minut. Dej vědět, jestli ti můžu s něčím ještě pomoct před tím.",
        "ar": "تنبيه فقط — ستُغلق هذه الجلسة خلال نحو عشر دقائق. أخبرني إذا كان هناك شيء آخر يمكنني المساعدة فيه قبل ذلك.",
        "ms": "Sekadar maklumat — sesi ini akan ditutup dalam kira-kira sepuluh minit. Beritahu saja kalau masih ada yang perlu dibantu sebelum itu.",
        "zh": "提醒一下 — 本次会话大约还有十分钟就会关闭。如果在那之前还需要任何帮助,随时告诉我。",
        "th": "ขอแจ้งให้ทราบค่ะ — เซสชันนี้จะปิดในอีกประมาณสิบนาที หากมีสิ่งใดให้ช่วยเพิ่มเติมก่อนหน้านั้น แจ้งได้เลยนะคะ",
    },
}


def render_canned(key: CannedKey, language_code: str) -> str:
    """Look up the canned utterance for a key + language. Falls back to
    English on unknown language codes."""
    bundle = _TRANSLATIONS[key]
    return bundle.get(language_code) or bundle["en"]
