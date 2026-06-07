// Tiny localization catalog for the install-free donor screen (Task 14.3).
//
// Mirrors the backend reviewed-template languages in
// backend/pulselink/messaging/templates.py (en / te / hi / ta) so the screen
// opened from an SMS/WhatsApp link reads in the donor's language, with an
// English fallback (Requirements 9.1, 9.3).
//
// These are DEMO translations for the offline hackathon flow; in production
// they would be replaced by professionally reviewed copy. Strings are short,
// warm, and contain NO medical claims (Requirement 6.1).

export const DEFAULT_LANG = "en";

// Languages supported by the donor screen, matching the backend catalog.
export const SUPPORTED_LANGS = ["en", "te", "hi", "ta"];

export const STRINGS = {
  en: {
    appName: "PulseLink",
    heading: "A patient near you may need blood",
    empathetic: "Your help could make this transfusion calm instead of an emergency. Thank you for being there.",
    windowLabel: "When",
    windowRange: "{start} to {end}",
    unitsLabel: "Units needed",
    accept: "Accept",
    decline: "Decline",
    sending: "Sending…",
    acceptedTitle: "Thank you",
    acceptedBody: "Your acceptance is recorded. The coordinator will share the details.",
    declinedTitle: "Recorded",
    declinedBody: "Thanks for letting us know. We will reach out to another donor.",
    errorTitle: "Something went wrong",
    errorBody: "We could not record your response. Please try again.",
    retry: "Try again",
    missingTitle: "Link not valid",
    missingBody: "This offer link is missing details. Please open the link from your message again.",
  },
  te: {
    appName: "PulseLink",
    heading: "మీ సమీపంలోని ఒక రోగికి రక్తం అవసరం కావచ్చు",
    empathetic: "మీ సహాయం ఈ రక్తమార్పిడిని అత్యవసర పరిస్థితి కాకుండా ప్రశాంతంగా చేయగలదు. మీరు అండగా ఉన్నందుకు ధన్యవాదాలు.",
    windowLabel: "ఎప్పుడు",
    windowRange: "{start} నుండి {end} వరకు",
    unitsLabel: "అవసరమైన యూనిట్లు",
    accept: "అంగీకరించు",
    decline: "తిరస్కరించు",
    sending: "పంపుతోంది…",
    acceptedTitle: "ధన్యవాదాలు",
    acceptedBody: "మీ అంగీకారం నమోదైంది. సమన్వయకర్త వివరాలను పంచుకుంటారు.",
    declinedTitle: "నమోదైంది",
    declinedBody: "తెలియజేసినందుకు ధన్యవాదాలు. మేము మరో దాతను సంప్రదిస్తాము.",
    errorTitle: "ఏదో తప్పు జరిగింది",
    errorBody: "మీ ప్రత్యుత్తరాన్ని నమోదు చేయలేకపోయాము. దయచేసి మళ్లీ ప్రయత్నించండి.",
    retry: "మళ్లీ ప్రయత్నించండి",
    missingTitle: "లింక్ చెల్లదు",
    missingBody: "ఈ ఆఫర్ లింక్‌లో వివరాలు లేవు. దయచేసి మీ సందేశం నుండి లింక్‌ను మళ్లీ తెరవండి.",
  },
  hi: {
    appName: "PulseLink",
    heading: "आपके पास के एक मरीज़ को रक्त की आवश्यकता हो सकती है",
    empathetic: "आपकी मदद इस रक्त-आधान को आपातकाल के बजाय शांत बना सकती है। आपके साथ होने के लिए धन्यवाद।",
    windowLabel: "कब",
    windowRange: "{start} से {end} तक",
    unitsLabel: "आवश्यक यूनिट",
    accept: "स्वीकार करें",
    decline: "अस्वीकार करें",
    sending: "भेजा जा रहा है…",
    acceptedTitle: "धन्यवाद",
    acceptedBody: "आपकी स्वीकृति दर्ज कर ली गई है। समन्वयक विवरण साझा करेंगे।",
    declinedTitle: "दर्ज किया गया",
    declinedBody: "बताने के लिए धन्यवाद। हम किसी अन्य दाता से संपर्क करेंगे।",
    errorTitle: "कुछ गड़बड़ हो गई",
    errorBody: "हम आपका उत्तर दर्ज नहीं कर सके। कृपया फिर से प्रयास करें।",
    retry: "फिर से प्रयास करें",
    missingTitle: "लिंक मान्य नहीं है",
    missingBody: "इस ऑफ़र लिंक में विवरण नहीं हैं। कृपया अपने संदेश से लिंक फिर से खोलें।",
  },
  ta: {
    appName: "PulseLink",
    heading: "உங்கள் அருகில் உள்ள ஒரு நோயாளிக்கு இரத்தம் தேவைப்படலாம்",
    empathetic: "உங்கள் உதவி இந்த இரத்தமாற்றத்தை அவசரநிலையாக இல்லாமல் அமைதியாக மாற்றும். நீங்கள் துணையாக இருப்பதற்கு நன்றி.",
    windowLabel: "எப்போது",
    windowRange: "{start} முதல் {end} வரை",
    unitsLabel: "தேவையான யூனிட்டுகள்",
    accept: "ஏற்கிறேன்",
    decline: "மறுக்கிறேன்",
    sending: "அனுப்புகிறது…",
    acceptedTitle: "நன்றி",
    acceptedBody: "உங்கள் ஒப்புதல் பதிவு செய்யப்பட்டது. ஒருங்கிணைப்பாளர் விவரங்களைப் பகிர்வார்.",
    declinedTitle: "பதிவு செய்யப்பட்டது",
    declinedBody: "தெரிவித்ததற்கு நன்றி. நாங்கள் மற்றொரு நன்கொடையாளரைத் தொடர்புகொள்வோம்.",
    errorTitle: "ஏதோ தவறு நடந்தது",
    errorBody: "உங்கள் பதிலை பதிவு செய்ய முடியவில்லை. மீண்டும் முயற்சிக்கவும்.",
    retry: "மீண்டும் முயற்சிக்கவும்",
    missingTitle: "இணைப்பு செல்லாது",
    missingBody: "இந்த சலுகை இணைப்பில் விவரங்கள் இல்லை. உங்கள் செய்தியிலிருந்து இணைப்பை மீண்டும் திறக்கவும்.",
  },
};

// Resolve the catalog language from a raw tag (e.g. URL ?lang=te), normalizing
// case/whitespace and falling back to English when unsupported or missing
// (Requirement 9.1, with the en fallback).
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

// BCP-47-ish lang attribute for the <html>/root element per resolved language.
export function htmlLangFor(lang) {
  return resolveLang(lang);
}
