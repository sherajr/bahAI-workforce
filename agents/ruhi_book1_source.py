"""
Curated source corpus for the Quote Card pipeline: every quotable passage from
Ruhi Institute Book 1, "Reflections on the Life of the Spirit" (edition
4.1.2.PE, May 2020), transcribed verbatim from the book's three units and
cross-checked against its own References list.

This is the ENTIRE and ONLY pool quote cards may draw their printed quote
from (owner decision, 2026-07) — see scripts/ingest_ruhi_book1.py, which
embeds this list into its own ChromaDB collection, and agents/librarian.py's
retrieve_ruhi_book1(). The bookmark pipeline is unaffected; it still searches
the full 7-text index via retrieve().

Deliberately excluded: administrative/definitional passages that answer a
procedural question (the precise biological moment of "conception", how many
times a day to recite an obligatory prayer) rather than offering a quotable,
beautiful idea — these read as correspondence, not as something you would
print on a small card for a stranger. Everything else block-quoted in the
book for reading and reflection is included, even where the book's own
citation is to a secondary compilation rather than a primary text; the
"source" field below always names the true speaker and work exactly as the
book's own References section does, and never fabricates or upgrades an
attribution the book itself did not make.

Each entry:
  text    — the passage, transcribed verbatim (spelling/punctuation as printed)
  source  — short citation for print: "{speaker}, {work}" (no locator)
  section — full locator + provenance, for internal verification only
            (never printed on a card)
"""

RUHI_BOOK1_QUOTES: list[dict] = [
    # ---- Unit 1: Understanding the Bahá'í Writings ----
    {
        "text": "The betterment of the world can be accomplished through pure and goodly deeds, "
                "through commendable and seemly conduct.",
        "source": "Bahá'u'lláh, cited in The Advent of Divine Justice",
        "section": "Ruhi Book 1, Unit 1 ref. 1 — Shoghi Effendi, The Advent of Divine Justice, par. 39, pp. 36–37",
    },
    {
        "text": "Beware, O people of Bahá, lest ye walk in the ways of them whose words differ from their deeds.",
        "source": "Bahá'u'lláh, Gleanings from the Writings of Bahá'u'lláh",
        "section": "Ruhi Book 1, Unit 1 ref. 2 — CXXXIX, par. 8, p. 345",
    },
    {
        "text": "O Son of Being! Bring thyself to account each day ere thou art summoned to a reckoning . . .",
        "source": "Bahá'u'lláh, The Hidden Words",
        "section": "Ruhi Book 1, Unit 1 ref. 3 — Arabic no. 31, p. 11",
    },
    {
        "text": "Say: O brethren! Let deeds, not words, be your adorning.",
        "source": "Bahá'u'lláh, The Hidden Words",
        "section": "Ruhi Book 1, Unit 1 ref. 4 — Persian no. 5, p. 24",
    },
    {
        "text": "Holy words and pure and goodly deeds ascend unto the heaven of celestial glory.",
        "source": "Bahá'u'lláh, The Hidden Words",
        "section": "Ruhi Book 1, Unit 1 ref. 5 — Persian no. 69, p. 46",
    },
    {
        "text": "Truthfulness is the foundation of all human virtues.",
        "source": "'Abdu'l-Bahá, cited in The Advent of Divine Justice",
        "section": "Ruhi Book 1, Unit 1 ref. 6 — Shoghi Effendi, The Advent of Divine Justice, par. 40, p. 39",
    },
    {
        "text": "Without truthfulness progress and success, in all the worlds of God, are impossible for any soul.",
        "source": "'Abdu'l-Bahá, cited in The Advent of Divine Justice",
        "section": "Ruhi Book 1, Unit 1 ref. 7 — Shoghi Effendi, The Advent of Divine Justice, par. 40, p. 39",
    },
    {
        "text": "Beautify your tongues, O people, with truthfulness, and adorn your souls with the ornament of honesty.",
        "source": "Bahá'u'lláh, Gleanings from the Writings of Bahá'u'lláh",
        "section": "Ruhi Book 1, Unit 1 ref. 8 — CXXXVI, par. 6, p. 336",
    },
    {
        "text": "Let your eye be chaste, your hand faithful, your tongue truthful and your heart enlightened.",
        "source": "Bahá'u'lláh, Tablets of Bahá'u'lláh Revealed after the Kitáb-i-Aqdas",
        "section": "Ruhi Book 1, Unit 1 ref. 9 — no. 9.5, p. 138",
    },
    {
        "text": "They who dwell within the tabernacle of God, and are established upon the seats of everlasting "
                "glory, will refuse, though they be dying of hunger, to stretch their hands and seize unlawfully "
                "the property of their neighbor, however vile and worthless he may be.",
        "source": "Bahá'u'lláh, Gleanings from the Writings of Bahá'u'lláh",
        "section": "Ruhi Book 1, Unit 1 ref. 10 — CXXXVII, par. 3, p. 338",
    },
    {
        "text": "A kindly tongue is the lodestone of the hearts of men. It is the bread of the spirit, it clotheth "
                "the words with meaning, it is the fountain of the light of wisdom and understanding.",
        "source": "Bahá'u'lláh, Gleanings from the Writings of Bahá'u'lláh",
        "section": "Ruhi Book 1, Unit 1 ref. 11 — CXXXII, par. 5, p. 327",
    },
    {
        "text": "O ye beloved of the Lord! In this sacred Dispensation, conflict and contention are in no wise "
                "permitted. Every aggressor deprives himself of God's grace.",
        "source": "'Abdu'l-Bahá, Will and Testament of 'Abdu'l-Bahá",
        "section": "Ruhi Book 1, Unit 1 ref. 12 — p. 26",
    },
    {
        "text": "Nothing whatever can, in this Day, inflict a greater harm upon this Cause than dissension and "
                "strife, contention, estrangement and apathy, among the loved ones of God.",
        "source": "Bahá'u'lláh, Gleanings from the Writings of Bahá'u'lláh",
        "section": "Ruhi Book 1, Unit 1 ref. 13 — V, par. 5, p. 8",
    },
    {
        "text": "Do not be content with showing friendship in words alone, let your heart burn with loving "
                "kindness for all who may cross your path.",
        "source": "'Abdu'l-Bahá, Paris Talks",
        "section": "Ruhi Book 1, Unit 1 ref. 14 — talk of 16–17 October 1911, no. 1.7, p. 6",
    },
    {
        "text": "When a thought of war comes, oppose it by a stronger thought of peace. A thought of hatred must "
                "be destroyed by a more powerful thought of love.",
        "source": "'Abdu'l-Bahá, Paris Talks",
        "section": "Ruhi Book 1, Unit 1 ref. 15 — talk of 21 October 1911, no. 6.7, p. 22",
    },
    {
        "text": ". . . backbiting quencheth the light of the heart, and extinguisheth the life of the soul.",
        "source": "Bahá'u'lláh, Gleanings from the Writings of Bahá'u'lláh",
        "section": "Ruhi Book 1, Unit 1 ref. 16 — CXXV, par. 3, p. 300",
    },
    {
        "text": "Breathe not the sins of others so long as thou art thyself a sinner.",
        "source": "Bahá'u'lláh, The Hidden Words",
        "section": "Ruhi Book 1, Unit 1 ref. 17 — Arabic no. 27, p. 10",
    },
    {
        "text": "Speak no evil, that thou mayest not hear it spoken unto thee, and magnify not the faults of others "
                "that thine own faults may not appear great . . .",
        "source": "Bahá'u'lláh, The Hidden Words",
        "section": "Ruhi Book 1, Unit 1 ref. 18 — Persian no. 44, p. 37",
    },
    {
        "text": "O Son of Being! How couldst thou forget thine own faults and busy thyself with the faults of others?",
        "source": "Bahá'u'lláh, The Hidden Words",
        "section": "Ruhi Book 1, Unit 1 ref. 19 — Arabic no. 26, p. 10",
    },
    {
        "text": "Immerse yourselves in the ocean of My words, that ye may unravel its secrets, and discover all the "
                "pearls of wisdom that lie hid in its depths.",
        "source": "Bahá'u'lláh, Gleanings from the Writings of Bahá'u'lláh",
        "section": "Ruhi Book 1, Unit 1 ref. 20 — LXX, par. 2, p. 154",
    },

    # ---- Unit 2: Prayer ----
    {
        "text": "Do not busy yourselves in your own concerns; let your thoughts be fixed upon that which will "
                "rehabilitate the fortunes of mankind and sanctify the hearts and souls of men.",
        "source": "Bahá'u'lláh, Gleanings from the Writings of Bahá'u'lláh",
        "section": "Ruhi Book 1, Unit 2 ref. 1 — XLIII, par. 4, p. 105",
    },
    {
        "text": ". . . the purpose for which mortal men have, from utter nothingness, stepped into the realm of "
                "being, is that they may work for the betterment of the world and live together in concord "
                "and harmony.",
        "source": "Bahá'u'lláh, in Trustworthiness: A Compilation of Extracts from the Bahá'í Writings",
        "section": "Ruhi Book 1, Unit 2 ref. 2 — no. 21, p. 5",
    },
    {
        "text": "A pure heart is as a mirror; cleanse it with the burnish of love and severance from all save God, "
                "that the true sun may shine therein and the eternal morning dawn.",
        "source": "Bahá'u'lláh, The Call of the Divine Beloved: Selected Mystical Works of Bahá'u'lláh",
        "section": "Ruhi Book 1, Unit 2 ref. 3 — no. 2.43, p. 31",
    },
    {
        "text": "Your hearts must be pure and your intentions sincere in order that you may become recipients of "
                "the divine bestowals.",
        "source": "'Abdu'l-Bahá, The Promulgation of Universal Peace",
        "section": "Ruhi Book 1, Unit 2 ref. 4 — talk of 5 May 1912, p. 127",
    },
    {
        "text": "O Son of Spirit! I created thee rich, why dost thou bring thyself down to poverty? Noble I made "
                "thee, wherewith dost thou abase thyself? Out of the essence of knowledge I gave thee being, "
                "why seekest thou enlightenment from anyone beside Me? Out of the clay of love I molded thee, "
                "how dost thou busy thyself with another? Turn thy sight unto thyself, that thou mayest find "
                "Me standing within thee, mighty, powerful and self-subsisting.",
        "source": "Bahá'u'lláh, The Hidden Words",
        "section": "Ruhi Book 1, Unit 2 ref. 5 — Arabic no. 13, pp. 6–7",
    },
    {
        "text": "the development of the individual and society, through the acquisition of spiritual virtues and "
                "powers. It is the soul of man that has first to be fed. And this spiritual nourishment prayer "
                "can best provide.",
        "source": "Shoghi Effendi, in Prayer and Devotional Life",
        "section": "Ruhi Book 1, Unit 2 ref. 6 — letter dated 8 December 1935, no. 71, p. 31",
    },
    {
        "text": "In the highest prayer, men pray only for the love of God, not because they fear Him or hell, or "
                "hope for bounty or heaven. . . . When a man falls in love with a human being, it is impossible "
                "for him to keep from mentioning the name of his beloved. How much more difficult is it to keep "
                "from mentioning the Name of God when one has come to love Him. . . . The spiritual man finds "
                "no delight in anything save in commemoration of God.",
        "source": "'Abdu'l-Bahá, cited in Bahá'u'lláh and the New Era by J. E. Esslemont",
        "section": "Ruhi Book 1, Unit 2 ref. 7 — p. 106",
    },
    {
        "text": "If one friend loves another, is it not natural that he should wish to say so? Though he knows "
                "that that friend is aware of his love, does he still not wish to tell him of it? . . . It is "
                "true that God knows the wishes of all hearts; but the impulse to pray is a natural one, "
                "springing from man's love to God.",
        "source": "'Abdu'l-Bahá, cited in Bahá'u'lláh and the New Era by J. E. Esslemont",
        "section": "Ruhi Book 1, Unit 2 ref. 8 — p. 106",
    },
    {
        "text": "I beseech Thee . . . to make of my prayer a fire that will burn away the veils which have shut "
                "me out from Thy beauty, and a light that will lead me unto the ocean of Thy Presence.",
        "source": "Bahá'u'lláh, in Bahá'í Prayers",
        "section": "Ruhi Book 1, Unit 2 ref. 9 — pp. 7–8",
    },
    {
        "text": "Make my prayer, O my Lord, a fountain of living waters whereby I may live as long as Thy "
                "sovereignty endureth, and may make mention of Thee in every world of Thy worlds.",
        "source": "Bahá'u'lláh, in Bahá'í Prayers",
        "section": "Ruhi Book 1, Unit 2 ref. 10 — p. 9",
    },
    {
        "text": "There is nothing sweeter in the world of existence than prayer. Man must live in a state of "
                "prayer. The most blessed condition is the condition of prayer and supplication. Prayer is "
                "conversation with God. The greatest attainment or the sweetest state is none other than "
                "conversation with God. It creates spirituality, creates mindfulness and celestial feelings, "
                "begets new attractions of the Kingdom and engenders the susceptibilities of the higher "
                "intelligence.",
        "source": "'Abdu'l-Bahá, quoted in Star of the West",
        "section": "Ruhi Book 1, Unit 2 ref. 11 — vol. 8, no. 4 (17 May 1917), p. 41",
    },
    {
        "text": "Intone, O My servant, the verses of God that have been received by thee, as intoned by them who "
                "have drawn nigh unto Him, that the sweetness of thy melody may kindle thine own soul, and "
                "attract the hearts of all men. Whoso reciteth, in the privacy of his chamber, the verses "
                "revealed by God, the scattering angels of the Almighty shall scatter abroad the fragrance of "
                "the words uttered by his mouth, and shall cause the heart of every righteous man to throb. "
                "Though he may, at first, remain unaware of its effect, yet the virtue of the grace vouchsafed "
                "unto him must needs sooner or later exercise its influence upon his soul. Thus have the "
                "mysteries of the Revelation of God been decreed by virtue of the Will of Him Who is the "
                "Source of power and wisdom.",
        "source": "Bahá'u'lláh, Gleanings from the Writings of Bahá'u'lláh",
        "section": "Ruhi Book 1, Unit 2 ref. 12 — CXXXVI, par. 2, p. 334",
    },
    {
        "text": "O God, my God! Look not upon my hopes and my doings, nay rather look upon Thy will that hath "
                "encompassed the heavens and the earth. By Thy Most Great Name, O Thou Lord of all nations! "
                "I have desired only what Thou didst desire, and love only what Thou dost love.",
        "source": "Bahá'u'lláh, in Bahá'í Prayers",
        "section": "Ruhi Book 1, Unit 2 ref. 13 — pp. 8–9",
    },
    {
        "text": "Too high art Thou for the praise of those who are nigh unto Thee to ascend unto the heaven of Thy "
                "nearness, or for the birds of the hearts of them who are devoted to Thee to attain to the "
                "door of Thy gate. I testify that Thou hast been sanctified above all attributes and holy "
                "above all names. No God is there but Thee, the Most Exalted, the All-Glorious.",
        "source": "Bahá'u'lláh, in Bahá'í Prayers",
        "section": "Ruhi Book 1, Unit 2 ref. 14 — p. 12",
    },
    {
        "text": "It behooveth the servant to pray to and seek assistance from God, and to supplicate and implore "
                "His aid. Such becometh the rank of servitude, and the Lord will decree whatsoever He desireth, "
                "in accordance with His consummate wisdom.",
        "source": "'Abdu'l-Bahá, in Prayer and Devotional Life",
        "section": "Ruhi Book 1, Unit 2 ref. 15 — no. 24, p. 7",
    },
    {
        "text": "Spirit has influence; prayer has spiritual effect. Therefore, we pray, 'O God! Heal this sick "
                "one!' Perchance God will answer. Does it matter who prays? God will answer the prayer of "
                "every servant if that prayer is urgent. His mercy is vast, illimitable. He answers the "
                "prayers of all His servants. He answers the prayer of this plant. The plant prays "
                "potentially, 'O God! Send me rain!' God answers the prayer, and the plant grows. God will "
                "answer anyone.",
        "source": "'Abdu'l-Bahá, The Promulgation of Universal Peace",
        "section": "Ruhi Book 1, Unit 2 ref. 16 — talk of 5 August 1912, p. 345",
    },
    {
        "text": "O thou who art turning thy face towards God! Close thine eyes to all things else, and open them "
                "to the realm of the All-Glorious. Ask whatsoever thou wishest of Him alone; seek whatsoever "
                "thou seekest from Him alone. With a look He granteth a hundred thousand hopes, with a glance "
                "He healeth a hundred thousand incurable ills, with a nod He layeth balm on every wound, with "
                "a glimpse He freeth the hearts from the shackles of grief. He doeth as He doeth, and what "
                "recourse have we? He carrieth out His Will, He ordaineth what He pleaseth. Then better for "
                "thee to bow down thy head in submission, and put thy trust in the All-Merciful Lord.",
        "source": "'Abdu'l-Bahá, Selections from the Writings of 'Abdu'l-Bahá",
        "section": "Ruhi Book 1, Unit 2 ref. 17 — no. 22.1, pp. 75–76",
    },
    {
        "text": "I bear witness, O my God, that Thou hast created me to know Thee and to worship Thee. I testify, "
                "at this moment, to my powerlessness and to Thy might, to my poverty and to Thy wealth.\n"
                "There is none other God but Thee, the Help in Peril, the Self-Subsisting.",
        "source": "Bahá'u'lláh, in Bahá'í Prayers",
        "section": "Ruhi Book 1, Unit 2 ref. 20 — Short Obligatory Prayer, p. 4",
    },
    {
        "text": "Gather ye together with the utmost joy and fellowship and recite the verses revealed by the "
                "merciful Lord. By so doing the doors of true knowledge will be opened to your inner beings, "
                "and ye will then feel your souls endowed with steadfastness and your hearts filled with "
                "radiant joy.",
        "source": "Bahá'u'lláh, in Prayer and Devotional Life",
        "section": "Ruhi Book 1, Unit 2 ref. 21 — no. 68, p. 29",
    },
    {
        "text": "Devotional meetings are occasions where any soul may enter, inhale the heavenly fragrances, "
                "experience the sweetness of prayer, meditate upon the Creative Word, be transported on the "
                "wings of the spirit, and commune with the one Beloved. Feelings of fellowship and common "
                "cause are generated, particularly in the spiritually heightened conversations that naturally "
                "occur at such times and through which the 'city of the human heart' may be opened.",
        "source": "The Universal House of Justice, in Framework for Action",
        "section": "Ruhi Book 1, Unit 2 ref. 22 — message dated 29 December 2015, no. 35.49, p. 232",
    },

    # ---- Unit 3: Life and Death ----
    {
        "text": "These material bodies are composed of atoms; when these atoms begin to separate decomposition "
                "sets in, then comes what we call death. . . . With the soul it is different. The soul is not "
                "a combination of elements, it is not composed of many atoms, it is of one indivisible "
                "substance and therefore eternal. It is entirely out of the order of the physical creation; "
                "it is immortal!",
        "source": "'Abdu'l-Bahá, Paris Talks",
        "section": "Ruhi Book 1, Unit 3 ref. 1 — talk of 10 November 1911, no. 29.12–13, p. 109",
    },
    {
        "text": "the rational soul, or the human spirit, does not subsist through this body by inherence—that is "
                "to say, it does not enter it; for inherence and entrance are characteristics of bodies, and "
                "the rational soul is sanctified above this. It never entered this body to begin with, that "
                "it should require, upon leaving it, some other abode. No, the connection of the spirit with "
                "the body is even as the connection of this lamp with a mirror. If the mirror is polished and "
                "perfected, the light of the lamp appears therein, and if the mirror is broken or covered "
                "with dust, the light remains concealed.",
        "source": "'Abdu'l-Bahá, Some Answered Questions",
        "section": "Ruhi Book 1, Unit 3 ref. 4 — no. 66.3, pp. 352–53",
    },
    {
        "text": "The human spirit has a beginning but no end: It endures forever.",
        "source": "'Abdu'l-Bahá, Some Answered Questions",
        "section": "Ruhi Book 1, Unit 3 ref. 5 — no. 38.5, p. 220",
    },
    {
        "text": "The spirit does not need a body, but the body needs spirit, or it cannot live. The soul can "
                "live without a body, but the body without a soul dies.",
        "source": "'Abdu'l-Bahá, Paris Talks",
        "section": "Ruhi Book 1, Unit 3 ref. 6 — talk of 9 November 1911, no. 28.16, p. 104",
    },
    {
        "text": "the human soul starts with the formation of the human embryo, and continues to develop and pass "
                "through endless stages of existence after its separation from the body. Its progress is thus "
                "infinite.",
        "source": "Shoghi Effendi, in Lights of Guidance",
        "section": "Ruhi Book 1, Unit 3 ref. 7 — letter dated 31 December 1937, no. 680, p. 204",
    },
    {
        "text": "Verily I say, the human soul is exalted above all egress and regress. It is still, and yet it "
                "soareth; it moveth, and yet it is still.",
        "source": "Bahá'u'lláh, Gleanings from the Writings of Bahá'u'lláh",
        "section": "Ruhi Book 1, Unit 3 ref. 8 — LXXXII, par. 8, p. 183",
    },
    {
        "text": "Know that the influence and perception of the human spirit is of two kinds; that is, the human "
                "spirit has two modes of operation and understanding. One mode is through the mediation of "
                "bodily instruments and organs. Thus it sees with the eye, hears with the ear, speaks with "
                "the tongue. . . . The other mode of the spirit's influence and action is without these "
                "bodily instruments and organs.",
        "source": "'Abdu'l-Bahá, Some Answered Questions",
        "section": "Ruhi Book 1, Unit 3 ref. 9 — no. 61.1–2, p. 334",
    },
    {
        "text": "Know thou that the soul of man is exalted above, and is independent of all infirmities of body "
                "or mind. That a sick person showeth signs of weakness is due to the hindrances that "
                "interpose themselves between his soul and his body, for the soul itself remaineth unaffected "
                "by any bodily ailments. Consider the light of the lamp. Though an external object may "
                "interfere with its radiance, the light itself continueth to shine with undiminished power. "
                "In like manner, every malady afflicting the body of man is an impediment that preventeth "
                "the soul from manifesting its inherent might and power. When it leaveth the body, however, "
                "it will evince such ascendancy, and reveal such influence as no force on earth can equal. "
                "Every pure, every refined and sanctified soul will be endowed with tremendous power, and "
                "shall rejoice with exceeding gladness.",
        "source": "Bahá'u'lláh, Gleanings from the Writings of Bahá'u'lláh",
        "section": "Ruhi Book 1, Unit 3 ref. 10 — LXXX, par. 2, p. 174",
    },
    {
        "text": "Know thou of a truth that the soul, after its separation from the body, will continue to "
                "progress until it attaineth the presence of God, in a state and condition which neither the "
                "revolution of ages and centuries, nor the changes and chances of this world, can alter. It "
                "will endure as long as the Kingdom of God, His sovereignty, His dominion and power will "
                "endure. It will manifest the signs of God and His attributes, and will reveal His loving "
                "kindness and bounty.",
        "source": "Bahá'u'lláh, Gleanings from the Writings of Bahá'u'lláh",
        "section": "Ruhi Book 1, Unit 3 ref. 11 — LXXXI, par. 1, p. 176",
    },
    {
        "text": "Death proffereth unto every confident believer the cup that is life indeed. It bestoweth joy, "
                "and is the bearer of gladness. It conferreth the gift of everlasting life.",
        "source": "Bahá'u'lláh, Gleanings from the Writings of Bahá'u'lláh",
        "section": "Ruhi Book 1, Unit 3 ref. 12 — CLXV, par. 2, pp. 391–92",
    },
    {
        "text": "O Son of the Supreme! I have made death a messenger of joy to thee. Wherefore dost thou grieve? "
                "I made the light to shed on thee its splendor. Why dost thou veil thyself therefrom?",
        "source": "Bahá'u'lláh, The Hidden Words",
        "section": "Ruhi Book 1, Unit 3 ref. 13 — Arabic no. 32, p. 11",
    },
    {
        "text": "In the beginning of his life man was in the world of the womb, wherein he developed the capacity "
                "and worthiness to advance to this world. The powers necessary for this world he acquired in "
                "that world. He needed eyes in this world; he obtained them in the world of the womb. . . . It "
                "followeth that in this world too he must prepare for the world beyond. That which he needeth "
                "in the world of the Kingdom he must obtain and prepare here.",
        "source": "'Abdu'l-Bahá, The Promulgation of Universal Peace",
        "section": "Ruhi Book 1, Unit 3 ref. 14 — talk of 6 July 1912, pp. 315–16",
    },
    {
        "text": "The whole duty of man in this Day is to attain that share of the flood of grace which God "
                "poureth forth for him. Let none, therefore, consider the largeness or smallness of the "
                "receptacle. The portion of some might lie in the palm of a man's hand, the portion of "
                "others might fill a cup, and of others even a gallon-measure.",
        "source": "Bahá'u'lláh, Gleanings from the Writings of Bahá'u'lláh",
        "section": "Ruhi Book 1, Unit 3 ref. 15 — V, par. 4, p. 8",
    },
    {
        "text": "Thou hast asked Me concerning the nature of the soul. Know, verily, that the soul is a sign of "
                "God, a heavenly gem whose reality the most learned of men hath failed to grasp, and whose "
                "mystery no mind, however acute, can ever hope to unravel. It is the first among all created "
                "things to declare the excellence of its Creator, the first to recognize His glory, to "
                "cleave to His truth, and to bow down in adoration before Him.",
        "source": "Bahá'u'lláh, Gleanings from the Writings of Bahá'u'lláh",
        "section": "Ruhi Book 1, Unit 3 ref. 16 — LXXXII, par. 1, pp. 179–80",
    },
    {
        "text": "Ye are even as the bird which soareth, with the full force of its mighty wings and with "
                "complete and joyous confidence, through the immensity of the heavens, until, impelled to "
                "satisfy its hunger, it turneth longingly to the water and clay of the earth below it, and, "
                "having been entrapped in the mesh of its desire, findeth itself impotent to resume its "
                "flight to the realms whence it came. . . . Wherefore, O My servants, defile not your wings with "
                "the clay of waywardness and vain desires, and suffer them not to be stained with the dust "
                "of envy and hate, that ye may not be hindered from soaring in the heavens of My divine "
                "knowledge.",
        "source": "Bahá'u'lláh, Gleanings from the Writings of Bahá'u'lláh",
        "section": "Ruhi Book 1, Unit 3 ref. 17 — CLIII, par. 6, pp. 370–71",
    },
    {
        "text": "He, through the direct operation of His unconstrained and sovereign Will, chose to confer "
                "upon man the unique distinction and capacity to know Him and to love Him—a capacity that "
                "must needs be regarded as the generating impulse and the primary purpose underlying the "
                "whole of creation. . . . Upon the reality of man, however, He hath focused the radiance of "
                "all of His names and attributes, and made it a mirror of His own Self. Alone of all created "
                "things man hath been singled out for so great a favor, so enduring a bounty.",
        "source": "Bahá'u'lláh, Gleanings from the Writings of Bahá'u'lláh",
        "section": "Ruhi Book 1, Unit 3 ref. 18 — XXVII, par. 2, pp. 72–73",
    },
    {
        "text": "These energies with which the Daystar of Divine bounty and Source of heavenly guidance hath "
                "endowed the reality of man lie, however, latent within him, even as the flame is hidden "
                "within the candle and the rays of light are potentially present in the lamp.",
        "source": "Bahá'u'lláh, Gleanings from the Writings of Bahá'u'lláh",
        "section": "Ruhi Book 1, Unit 3 ref. 19 — XXVII, par. 3, p. 73",
    },
    {
        "text": "As a token of His mercy, however, and as a proof of His loving-kindness, He hath manifested "
                "unto men the Daystars of His divine guidance, the Symbols of His divine unity, and hath "
                "ordained the knowledge of these sanctified Beings to be identical with the knowledge of His "
                "own Self. Whoso recognizeth them hath recognized God.",
        "source": "Bahá'u'lláh, Gleanings from the Writings of Bahá'u'lláh",
        "section": "Ruhi Book 1, Unit 3 ref. 20 — XXI, par. 1, pp. 54–55",
    },
    {
        "text": "The Prophets and Messengers of God have been sent down for the sole purpose of guiding mankind "
                "to the straight Path of Truth. The purpose underlying Their revelation hath been to educate "
                "all men, that they may, at the hour of death, ascend, in the utmost purity and sanctity and "
                "with absolute detachment, to the throne of the Most High.",
        "source": "Bahá'u'lláh, Gleanings from the Writings of Bahá'u'lláh",
        "section": "Ruhi Book 1, Unit 3 ref. 21 — LXXXI, par. 1, p. 177",
    },
    {
        "text": "Man is the supreme Talisman. Lack of a proper education hath, however, deprived him of that "
                "which he doth inherently possess. . . . Regard man as a mine rich in gems of inestimable "
                "value. Education can, alone, cause it to reveal its treasures, and enable mankind to "
                "benefit therefrom.",
        "source": "Bahá'u'lláh, Gleanings from the Writings of Bahá'u'lláh",
        "section": "Ruhi Book 1, Unit 3 ref. 22 — CXXII, par. 1, pp. 293–94",
    },
    {
        "text": "if the soul of man hath walked in the ways of God, it will, assuredly, return and be gathered "
                "to the glory of the Beloved. By the righteousness of God! It shall attain a station such as "
                "no pen can depict, or tongue describe.",
        "source": "Bahá'u'lláh, Gleanings from the Writings of Bahá'u'lláh",
        "section": "Ruhi Book 1, Unit 3 ref. 23 — LXXXII, par. 7, p. 182",
    },
    {
        "text": "Blessed is the soul which, at the hour of its separation from the body, is sanctified from the "
                "vain imaginings of the peoples of the world. Such a soul liveth and moveth in accordance "
                "with the Will of its Creator, and entereth the all-highest Paradise.",
        "source": "Bahá'u'lláh, Gleanings from the Writings of Bahá'u'lláh",
        "section": "Ruhi Book 1, Unit 3 ref. 24 — LXXXI, par. 1, pp. 176–77",
    },
    {
        "text": "He should forgive the sinful, and never despise his low estate, for none knoweth what his own "
                "end shall be. How often hath a sinner attained, at the hour of death, to the essence of "
                "faith, and, quaffing the immortal draught, hath taken his flight unto the Concourse on high!",
        "source": "Bahá'u'lláh, Gleanings from the Writings of Bahá'u'lláh",
        "section": "Ruhi Book 1, Unit 3 ref. 25 — CXXV, par. 3, pp. 300–1",
    },
    {
        "text": "As the spirit of man lives forever after casting off this elemental frame, it is, like all "
                "existing things, undoubtedly capable of progress, and thus one may pray for a departed soul "
                "to advance, to be forgiven, or to be made the recipient of divine favors, bounties, and "
                "grace.",
        "source": "'Abdu'l-Bahá, Some Answered Questions",
        "section": "Ruhi Book 1, Unit 3 ref. 26 — no. 62.3, pp. 340–41",
    },
    {
        "text": "When the human soul soareth out of this transient heap of dust and riseth into the world of "
                "God, then veils will fall away, and verities will come to light, and all things unknown "
                "before will be made clear, and hidden truths be understood.",
        "source": "'Abdu'l-Bahá, Selections from the Writings of 'Abdu'l-Bahá",
        "section": "Ruhi Book 1, Unit 3 ref. 27 — no. 149.3–4, pp. 246–47",
    },
    {
        "text": "the souls of the people of Bahá, who have entered and been established within the Crimson Ark, "
                "shall associate and commune intimately one with another, and shall be so closely associated "
                "in their lives, their aspirations, their aims and strivings as to be even as one soul.",
        "source": "Bahá'u'lláh, Gleanings from the Writings of Bahá'u'lláh",
        "section": "Ruhi Book 1, Unit 3 ref. 28 — LXXXVI, par. 1, pp. 192–93",
    },
    {
        "text": "O My servants! Sorrow not if, in these days and on this earthly plane, things contrary to your "
                "wishes have been ordained and manifested by God, for days of blissful joy, of heavenly "
                "delight, are assuredly in store for you. Worlds, holy and spiritually glorious, will be "
                "unveiled to your eyes.",
        "source": "Bahá'u'lláh, Gleanings from the Writings of Bahá'u'lláh",
        "section": "Ruhi Book 1, Unit 3 ref. 29 — CLIII, par. 9, p. 373",
    },
]
