# Interface Autoresearch

Interface React et TypeScript du superviseur local. Aucun jeu de données de démonstration n'est injecté à l'exécution : les campagnes, budgets et preuves proviennent de `/api`.

Depuis `web/`, avec Node.js 22 ou supérieur :

```sh
npm ci
npm run dev
```

Vite relaie `/api` vers `http://127.0.0.1:8765`. En production, `npm run build` génère `web/dist`, servi par l'API Python. Les polices sont incluses dans le build, sans requête externe.

```sh
npm test
npm run build
```

Les tests couvrent les erreurs du superviseur, l'idempotence des mutations, la précision micro-USD, les transitions des commandes, l'approbation attachée à un hash et le refus d'une garantie Claude non vérifiée. Seuls ces tests utilisent des réponses simulées.

L'interface propose les campagnes manuelles par défaut. Le mode Claude strict reste bloqué lorsque les capacités du fournisseur ne démontrent pas cette garantie. Le choix du mode estimatif et ses limites sont explicites. La publication d'une draft PR demande la revue d'un candidat confirmé, de son contenu et de la destination ; aucun écran ne publie automatiquement.
