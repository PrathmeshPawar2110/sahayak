export type Lang = "hi" | "mr" | "en";

export const LANGUAGES: { code: Lang; label: string }[] = [
  { code: "hi", label: "हिंदी" },
  { code: "mr", label: "मराठी" },
  { code: "en", label: "English" },
];

const en = {
  appName: "Sahayak",
  tagline: "Find government schemes you qualify for",
  languagePickerLabel: "Choose your language",
  consentTitle: "Before we start",
  consentBody:
    "Sahayak will remember what you tell it — like your age, income or occupation — so it can check which government schemes you qualify for. You can see everything it knows, and delete it at any time.",
  consentSensitiveNote:
    'Some questions (like caste category or disability) are optional. You can always say "I\'d rather not say."',
  consentAccept: "I agree, let's start",
  chatPlaceholder: "Type your message…",
  send: "Send",
  micStart: "Speak",
  micStop: "Stop",
  listening: "Listening…",
  thinking: "Thinking…",
  profileButton: "What Sahayak knows about me",
  profileTitle: "What Sahayak knows about me",
  profileEmpty: "Nothing yet. Keep chatting and Sahayak will remember what helps.",
  profileEditHint:
    'To correct this, just tell Sahayak in the chat — for example, "My income is actually ₹2,00,000."',
  profileDelete: "Delete everything",
  profileDeleteConfirmTitle: "Delete everything?",
  profileDeleteConfirmBody:
    "This removes your conversation and everything Sahayak knows about you. This cannot be undone.",
  cancel: "Cancel",
  close: "Close",
  eligibilityReady: "You can apply",
  eligibilityActionNeeded: "Eligible — action needed",
  eligibilityProvisional: "Likely eligible (not yet verified)",
  provisionalCaveat:
    "We found this scheme recently and haven't fully verified its rules yet. Check the official link before relying on this.",
  lastVerified: "Last verified",
  officialLink: "Official page",
  sources: "Sources",
  startOver: "Start over",
  errorConsentRequired: "Please agree to the privacy notice before chatting.",
  errorGeneric: "Something went wrong. Please try again.",
  errorMicPermission:
    "Sahayak needs microphone access to listen. Please allow it in your browser.",
  playReply: "Play this reply",
  you: "You",
  sahayak: "Sahayak",
};

type Key = keyof typeof en;

const hi: Record<Key, string> = {
  appName: "सहायक",
  tagline: "आपके लिए उपयुक्त सरकारी योजनाएँ खोजें",
  languagePickerLabel: "अपनी भाषा चुनें",
  consentTitle: "शुरू करने से पहले",
  consentBody:
    "सहायक आपकी बताई बातें याद रखेगा — जैसे आपकी उम्र, आय या पेशा — ताकि यह जांच सके कि आप किन सरकारी योजनाओं के योग्य हैं। आप किसी भी समय देख सकते हैं कि इसे क्या पता है, और इसे मिटा सकते हैं।",
  consentSensitiveNote:
    'कुछ सवाल (जैसे जाति श्रेणी या दिव्यांगता) वैकल्पिक हैं। आप हमेशा कह सकते हैं "मैं नहीं बताना चाहता/चाहती"।',
  consentAccept: "मैं सहमत हूँ, शुरू करें",
  chatPlaceholder: "अपना संदेश लिखें…",
  send: "भेजें",
  micStart: "बोलें",
  micStop: "रोकें",
  listening: "सुन रहा हूँ…",
  thinking: "सोच रहा हूँ…",
  profileButton: "सहायक मेरे बारे में क्या जानता है",
  profileTitle: "सहायक मेरे बारे में क्या जानता है",
  profileEmpty: "अभी कुछ नहीं। बातचीत जारी रखें, सहायक ज़रूरी बातें याद रखेगा।",
  profileEditHint:
    'इसे ठीक करने के लिए, चैट में सहायक को बताएं — उदाहरण: "मेरी आय वास्तव में ₹2,00,000 है।"',
  profileDelete: "सब कुछ मिटाएं",
  profileDeleteConfirmTitle: "सब कुछ मिटाएं?",
  profileDeleteConfirmBody:
    "इससे आपकी बातचीत और सहायक के पास मौजूद आपकी सारी जानकारी हट जाएगी। यह वापस नहीं लिया जा सकता।",
  cancel: "रद्द करें",
  close: "बंद करें",
  eligibilityReady: "आप आवेदन कर सकते हैं",
  eligibilityActionNeeded: "योग्य — कार्रवाई आवश्यक",
  eligibilityProvisional: "संभावित रूप से योग्य (अभी सत्यापित नहीं)",
  provisionalCaveat:
    "हमें यह योजना हाल में मिली है और हमने अभी तक इसके नियमों को पूरी तरह सत्यापित नहीं किया है। इस पर भरोसा करने से पहले आधिकारिक लिंक देखें।",
  lastVerified: "अंतिम सत्यापन",
  officialLink: "आधिकारिक पृष्ठ",
  sources: "स्रोत",
  startOver: "फिर से शुरू करें",
  errorConsentRequired: "कृपया बातचीत से पहले गोपनीयता सूचना से सहमत हों।",
  errorGeneric: "कुछ गड़बड़ हो गई। कृपया फिर से प्रयास करें।",
  errorMicPermission: "सुनने के लिए सहायक को माइक्रोफ़ोन की अनुमति चाहिए। कृपया अपने ब्राउज़र में इसकी अनुमति दें।",
  playReply: "यह जवाब सुनें",
  you: "आप",
  sahayak: "सहायक",
};

const mr: Record<Key, string> = {
  appName: "सहायक",
  tagline: "तुमच्यासाठी योग्य सरकारी योजना शोधा",
  languagePickerLabel: "तुमची भाषा निवडा",
  consentTitle: "सुरू करण्यापूर्वी",
  consentBody:
    "सहायक तुम्ही सांगितलेल्या गोष्टी लक्षात ठेवेल — जसे तुमचे वय, उत्पन्न किंवा व्यवसाय — जेणेकरून तुम्ही कोणत्या सरकारी योजनांसाठी पात्र आहात हे तपासता येईल. तुम्ही कधीही पाहू शकता की त्याला काय माहीत आहे, आणि ते कधीही मिटवू शकता.",
  consentSensitiveNote:
    'काही प्रश्न (जसे जात प्रवर्ग किंवा अपंगत्व) ऐच्छिक आहेत. तुम्ही केव्हाही म्हणू शकता "मला सांगायचे नाही".',
  consentAccept: "मी सहमत आहे, सुरू करा",
  chatPlaceholder: "तुमचा संदेश टाइप करा…",
  send: "पाठवा",
  micStart: "बोला",
  micStop: "थांबा",
  listening: "ऐकत आहे…",
  thinking: "विचार करत आहे…",
  profileButton: "सहायकला माझ्याबद्दल काय माहीत आहे",
  profileTitle: "सहायकला माझ्याबद्दल काय माहीत आहे",
  profileEmpty: "अजून काही नाही. गप्पा सुरू ठेवा, सहायक उपयुक्त गोष्टी लक्षात ठेवेल.",
  profileEditHint:
    'हे सुधारण्यासाठी, चॅटमध्ये सहायकला सांगा — उदाहरणार्थ, "माझे उत्पन्न खरे तर ₹2,00,000 आहे."',
  profileDelete: "सर्व काही हटवा",
  profileDeleteConfirmTitle: "सर्व काही हटवायचे?",
  profileDeleteConfirmBody:
    "यामुळे तुमची चॅट आणि सहायककडे असलेली तुमची सर्व माहिती काढली जाईल. हे पूर्ववत करता येणार नाही.",
  cancel: "रद्द करा",
  close: "बंद करा",
  eligibilityReady: "तुम्ही अर्ज करू शकता",
  eligibilityActionNeeded: "पात्र — कृती आवश्यक",
  eligibilityProvisional: "शक्यतो पात्र (अजून तपासलेले नाही)",
  provisionalCaveat:
    "आम्हाला ही योजना अलीकडे सापडली आहे आणि आम्ही अजून तिचे नियम पूर्णपणे तपासलेले नाहीत. यावर विश्वास ठेवण्यापूर्वी अधिकृत लिंक पाहा.",
  lastVerified: "शेवटचे सत्यापन",
  officialLink: "अधिकृत पृष्ठ",
  sources: "स्रोत",
  startOver: "पुन्हा सुरू करा",
  errorConsentRequired: "कृपया गप्पा मारण्यापूर्वी गोपनीयता सूचनेला सहमती द्या.",
  errorGeneric: "काहीतरी चुकले. कृपया पुन्हा प्रयत्न करा.",
  errorMicPermission: "ऐकण्यासाठी सहायकला मायक्रोफोनची परवानगी हवी. कृपया तुमच्या ब्राउझरमध्ये ती द्या.",
  playReply: "हे उत्तर ऐका",
  you: "तुम्ही",
  sahayak: "सहायक",
};

export const strings: Record<Lang, Record<Key, string>> = { en, hi, mr };

export type StringKey = Key;
