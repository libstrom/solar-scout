-- Enspecta Energi befintliga installationer
-- Byggnader inom 30m av dessa koordinater skippas vid scanning.
-- Källa: Enspectas installationshistorik, importerad 2026-05-20.
CREATE TABLE IF NOT EXISTS enspecta_installations (
    id      BIGSERIAL PRIMARY KEY,
    lat     DOUBLE PRECISION NOT NULL,
    lng     DOUBLE PRECISION NOT NULL,
    address TEXT NOT NULL
);

-- Seed: 45 verifierade adresser
INSERT INTO enspecta_installations (lat, lng, address) VALUES
    (55.530048, 13.093632, 'Ida Nilssons gata 28, Oxie'),
    (55.590278, 13.068015, 'Mandelpilsgatan 44, Malmö'),
    (55.520124, 13.003448, 'Mittskeppsgatan 18, Malmö'),
    (55.971099, 12.763006, 'Svanögatan 13, Rydebäck'),
    (55.594025, 13.061326, 'Byhögsgatan 6, Malmö'),
    (55.672031, 13.077517, 'Algatan 8, Lomma'),
    (55.578492, 13.046422, 'Örnehuvuds väg 29, Malmö'),
    (55.497469, 13.185753, 'Skönadalsvägen 7, Svedala'),
    (55.646319, 13.223985, 'Stenbockens Väg 8, Staffanstorp'),
    (55.793515, 13.439441, 'Tunnbindarvägen 16, Hurva'),
    (55.578225, 13.042659, 'Spireagatan 3, Malmö'),
    (55.626138, 14.152732, 'Ljunglyckevägen 44, Sankt Olof'),
    (55.529977, 13.074765, 'Anders Orkans väg 18, Oxie'),
    (55.620516, 13.202155, 'Grusvägen 15, Staffanstorp'),
    (55.574238, 13.028630, 'Västra Hindbyvägen 57, Malmö'),
    (55.570583, 13.011091, 'Ekhagegatan 13, Malmö'),
    (55.633662, 13.704475, 'Tvärgatan 4, Sjöbo'),
    (55.809976, 13.592018, 'Söderto 3295, Hörby'),
    (55.716469, 13.130792, 'Gamlemark 721, Lund'),
    (55.585930, 12.963956, 'Midgårdstorget 2, Malmö'),
    (55.491573, 13.130583, 'Gamlesjövägen 90, Vellinge'),
    (55.369109, 13.185104, 'Östra Förstadsgatan 80, Trelleborg'),
    (55.597086, 13.077720, 'Harbackegatan 26, Malmö'),
    (55.668390, 13.344204, 'Domaregatan 7, Dalby'),
    (55.573323, 12.994148, 'Irisgatan 22, Malmö'),
    (55.572066, 12.990332, 'Majgatan 2, Malmö'),
    (55.570583, 13.011091, 'Ekhagegatan 10, Malmö'),
    (55.446303, 14.017706, 'Norråvägen 10, Glemmingebro'),
    (55.575916, 13.035974, 'Furugatan 5, Malmö'),
    (55.550254, 13.935495, 'Sprintergatan 9, Tomelilla'),
    (55.539202, 13.080537, 'Planetgatan 22, Oxie'),
    (55.590978, 13.236721, 'Talltitegränd 7, Klågerup'),
    (55.572390, 13.031471, 'Silverviksgatan 11, Malmö'),
    (55.636674, 13.101630, 'Egnahemsvägen 23, Arlöv'),
    (55.574648, 13.013402, 'Axgatan 11, Malmö'),
    (55.591824, 13.090270, 'Bullerbygatan 83, Malmö'),
    (55.575115, 13.068338, 'Skimmelgatan 9, Malmö'),
    (55.756222, 13.013768, 'Backavägen 24, Löddeköpinge'),
    (55.570847, 12.993317, 'Lyckebogatan 16, Malmö'),
    (55.638249, 13.101038, 'Irisvägen 4, Arlöv'),
    (55.431808, 13.947073, 'Bassängvägen 11, Nybrostrand'),
    (55.543068, 13.073134, 'Fänkålsvägen 74, Oxie'),
    (55.586413, 13.185917, 'Fontänpilsgränd 16, Bara'),
    (55.661441, 13.105102, 'Herrestadsvägen 5, Åkarp'),
    (55.390168, 13.142956, 'Sven Tveskäggs Väg 18, Trelleborg')
ON CONFLICT DO NOTHING;
