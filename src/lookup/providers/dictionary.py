"""Dictionary lookup provider."""

from urllib.parse import quote

from ..provider import (
    JSONValue,
    Link,
    ProviderBase,
    Result,
    jsonArray,
    jsonObject,
    jsonString,
)


class DictionaryProvider(ProviderBase):
    title = "Dictionary"
    missingOk = True
    lowercase = True

    @staticmethod
    def url(query: str) -> str:
        return f"https://api.dictionaryapi.dev/api/v2/entries/en/{query}"

    @staticmethod
    def parseResult(data: JSONValue | None) -> Result | None:
        """Extract the first entry and up to four meanings."""
        if data is None:
            return None
        entries = jsonArray(data)
        if not entries:
            return None
        entry = jsonObject(entries[0])
        word = jsonString(entry.get("word"))
        definitions = []
        for item in jsonArray(entry.get("meanings", []))[:4]:
            meaning = jsonObject(item)
            meanings = jsonArray(meaning.get("definitions", []))
            if meanings:
                part = jsonString(meaning.get("partOfSpeech"))
                definition = jsonString(jsonObject(meanings[0]).get("definition"))
                definitions.append(f"{part}: {definition}")
        urls = entry.get("sourceUrls")
        fallbackUrl = "https://en.wiktionary.org/wiki/" + quote(word, safe="")
        url = (
            next((item for item in urls if isinstance(item, str) and item), fallbackUrl)
            if isinstance(urls, list)
            else fallbackUrl
        )
        phonetic = entry.get("phonetic")
        return Result(
            title=word,
            detail=phonetic if isinstance(phonetic, str) else "",
            text="\n\n".join(definitions),
            links=(Link("Wiktionary", url),),
        )


Provider = DictionaryProvider
