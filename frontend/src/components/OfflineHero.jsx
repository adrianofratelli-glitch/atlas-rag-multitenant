import Card from '@leafygreen-ui/card'
import Button from '@leafygreen-ui/button'
import { MongoDBLogoMark } from '@leafygreen-ui/logo'
import { H3, Body } from '@leafygreen-ui/typography'
import { C } from '../theme'

export default function OfflineHero({ dbName, onReconnect, reconnecting }) {
  return (
    <Card darkMode style={{ padding: '28px 30px', textAlign: 'center', border: '1px solid rgba(255,105,96,0.25)' }}>
      <div
        style={{
          width: 46, height: 46, margin: '0 auto 14px', borderRadius: 12,
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          background: 'rgba(255,105,96,0.08)', border: '1px solid rgba(255,105,96,0.25)',
        }}
      >
        <MongoDBLogoMark height={22} />
      </div>
      <H3 darkMode style={{ marginBottom: 8 }}>Cluster MongoDB Atlas indisponível</H3>
      <Body darkMode style={{ color: C.sub, maxWidth: 540, margin: '0 auto 18px' }}>
        A POC não conseguiu conectar ao cluster <code style={{ color: C.green }}>{dbName}</code>.
        Provavelmente ele está <strong>pausado</strong> (o tier gratuito pausa após inatividade) ou
        seu <strong>IP não está liberado</strong> na Access List. Reative o cluster no Atlas e clique
        abaixo para reconectar.
      </Body>
      <Button darkMode variant="primary" onClick={onReconnect} disabled={reconnecting}>
        {reconnecting ? 'Verificando…' : 'Tentar reconectar ao Atlas'}
      </Button>
    </Card>
  )
}
