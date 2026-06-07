// Tiny localization catalog for the calm patient/parent home screen (Task 14.1).
//
// Mirrors the donor screen's catalog approach (frontend/donor-screen/src/i18n.js)
// and the backend reviewed-template languages (en / te / hi / ta) so a patient or
// parent reads the reassurance in their own language, with an English fallback.
//
// These are DEMO translations for the offline hackathon flow; in production they
// would be replaced by professionally reviewed copy. The copy is intentionally
// CALM and anxiety-reducing and contains NO medical claims: it only ever tells
// the family whether the next transfusion's blood is "arranged" or "arranging"
// (Requirements 1.1, 1.2), and shows the next transfusion Window as a date range
// (Requirement 1.3).

export const DEFAULT_LANG = "en";

// Languages supported by the patient screen, matching the backend catalog.
export const SUPPORTED_LANGS = ["en", "te", "hi", "ta"];

export const STRINGS = {
  en: {
    appName: "PulseLink",
    greeting: "Your care plan",
    reassuranceArranged:
      "Blood for the next transfusion is arranged. There is nothing you need to do.",
    reassuranceArranging:
      "We are arranging blood for the next transfusion. We will keep this updated — you do not need to chase anyone.",
    statusLabel: "Next transfusion",
    statusArranged: "Arranged",
    statusArranging: "Arranging",
    windowLabel: "Expected window",
    windowRange: "{start} to {end}",
    noWindow: "Your next window will appear here once it is scheduled.",
    footer: "PulseLink keeps your transfusions planned ahead, so each cycle stays calm.",
    loadingTitle: "Loading your plan…",
    errorTitle: "We could not load your plan",
    errorBody: "Please open the link from your message again, or try once more.",
    retry: "Try again",
    missingTitle: "Link not valid",
    missingBody:
      "This page is missing the details that identify your plan. Please open the link from your message again.",
  },
  te: {
    appName: "PulseLink",
    greeting: "మీ సంరక్షణ ప్రణాళిక",
    reassuranceArranged:
      "తదుపరి రక్తమార్పిడికి రక్తం సిద్ధం చేయబడింది. మీరు చేయవలసినది ఏమీ లేదు.",
    reassuranceArranging:
      "తదుపరి రక్తమార్పిడికి మేము రక్తం ఏర్పాటు చేస్తున్నాము. దీన్ని నవీకరిస్తూ ఉంటాము — మీరు ఎవరినీ వెంబడించాల్సిన అవసరం లేదు.",
    statusLabel: "తదుపరి రక్తమార్పిడి",
    statusArranged: "సిద్ధమైంది",
    statusArranging: "ఏర్పాటవుతోంది",
    windowLabel: "ఊహించిన సమయం",
    windowRange: "{start} నుండి {end} వరకు",
    noWindow: "షెడ్యూల్ చేయబడిన తర్వాత మీ తదుపరి సమయం ఇక్కడ కనిపిస్తుంది.",
    footer:
      "PulseLink మీ రక్తమార్పిడులను ముందుగానే ప్రణాళిక చేస్తుంది, తద్వారా ప్రతి చక్రం ప్రశాంతంగా ఉంటుంది.",
    loadingTitle: "మీ ప్రణాళికను లోడ్ చేస్తోంది…",
    errorTitle: "మీ ప్రణాళికను లోడ్ చేయలేకపోయాము",
    errorBody: "దయచేసి మీ సందేశం నుండి లింక్‌ను మళ్లీ తెరవండి, లేదా మరోసారి ప్రయత్నించండి.",
    retry: "మళ్లీ ప్రయత్నించండి",
    missingTitle: "లింక్ చెల్లదు",
    missingBody:
      "ఈ పేజీలో మీ ప్రణాళికను గుర్తించే వివరాలు లేవు. దయచేసి మీ సందేశం నుండి లింక్‌ను మళ్లీ తెరవండి.",
  },
  hi: {
    appName: "PulseLink",
    greeting: "आपकी देखभाल योजना",
    reassuranceArranged:
      "अगले रक्त-आधान के लिए रक्त की व्यवस्था हो गई है। आपको कुछ करने की आवश्यकता नहीं है।",
    reassuranceArranging:
      "हम अगले रक्त-आधान के लिए रक्त की व्यवस्था कर रहे हैं। हम इसे अपडेट करते रहेंगे — आपको किसी के पीछे भागने की आवश्यकता नहीं है।",
    statusLabel: "अगला रक्त-आधान",
    statusArranged: "व्यवस्थित",
    statusArranging: "व्यवस्था की जा रही है",
    windowLabel: "अपेक्षित समय",
    windowRange: "{start} से {end} तक",
    noWindow: "निर्धारित होने पर आपका अगला समय यहाँ दिखाई देगा।",
    footer:
      "PulseLink आपके रक्त-आधान की योजना पहले से बनाता है, ताकि हर चक्र शांत रहे।",
    loadingTitle: "आपकी योजना लोड हो रही है…",
    errorTitle: "हम आपकी योजना लोड नहीं कर सके",
    errorBody: "कृपया अपने संदेश से लिंक फिर से खोलें, या एक बार और प्रयास करें।",
    retry: "फिर से प्रयास करें",
    missingTitle: "लिंक मान्य नहीं है",
    missingBody:
      "इस पृष्ठ में आपकी योजना की पहचान करने वाले विवरण नहीं हैं। कृपया अपने संदेश से लिंक फिर से खोलें।",
  },
  ta: {
    appName: "PulseLink",
    greeting: "உங்கள் பராமரிப்புத் திட்டம்",
    reassuranceArranged:
      "அடுத்த இரத்தமாற்றத்திற்கு இரத்தம் ஏற்பாடு செய்யப்பட்டுள்ளது. நீங்கள் எதுவும் செய்ய வேண்டியதில்லை.",
    reassuranceArranging:
      "அடுத்த இரத்தமாற்றத்திற்கு நாங்கள் இரத்தத்தை ஏற்பாடு செய்து வருகிறோம். இதைப் புதுப்பித்துக்கொண்டே இருப்போம் — நீங்கள் யாரையும் தொடர்ந்து கேட்க வேண்டியதில்லை.",
    statusLabel: "அடுத்த இரத்தமாற்றம்",
    statusArranged: "ஏற்பாடாகிவிட்டது",
    statusArranging: "ஏற்பாடாகிறது",
    windowLabel: "எதிர்பார்க்கப்படும் காலம்",
    windowRange: "{start} முதல் {end} வரை",
    noWindow: "திட்டமிடப்பட்டவுடன் உங்கள் அடுத்த காலம் இங்கே தோன்றும்.",
    footer:
      "PulseLink உங்கள் இரத்தமாற்றங்களை முன்கூட்டியே திட்டமிடுகிறது, இதனால் ஒவ்வொரு சுழற்சியும் அமைதியாக இருக்கும்.",
    loadingTitle: "உங்கள் திட்டம் ஏற்றப்படுகிறது…",
    errorTitle: "உங்கள் திட்டத்தை ஏற்ற முடியவில்லை",
    errorBody: "உங்கள் செய்தியிலிருந்து இணைப்பை மீண்டும் திறக்கவும், அல்லது மீண்டும் முயற்சிக்கவும்.",
    retry: "மீண்டும் முயற்சிக்கவும்",
    missingTitle: "இணைப்பு செல்லாது",
    missingBody:
      "இந்தப் பக்கத்தில் உங்கள் திட்டத்தை அடையாளம் காணும் விவரங்கள் இல்லை. உங்கள் செய்தியிலிருந்து இணைப்பை மீண்டும் திறக்கவும்.",
  },
};

// Resolve the catalog language from a raw tag (e.g. URL ?lang=te), normalizing
// case/whitespace and falling back to English when unsupported or missing.
export function resolveLang(rawLang) {
  if (typeof rawLang !== "string") {
    return DEFAULT_LANG;
  }
  const normalized = rawLang.trim().toLowerCase();
  return SUPPORTED_LANGS.includes(normalized) ? normalized : DEFAULT_LANG;
}

// Return the string bundle for a (already-resolved or raw) language tag.
export function getStrings(lang) {
  return STRINGS[resolveLang(lang)];
}

// Minimal {placeholder} interpolation for the catalog strings.
export function format(template, values) {
  return template.replace(/\{(\w+)\}/g, (match, key) =>
    Object.prototype.hasOwnProperty.call(values, key) ? String(values[key]) : match
  );
}
