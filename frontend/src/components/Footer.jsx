import { MongoDBLogoMark } from '@leafygreen-ui/logo'

export default function Footer() {
  return (
    <div className="mdb-footer">
      <MongoDBLogoMark height={16} />
      <span className="mdb-footer-txt">
        Powered by <strong>MongoDB Atlas</strong> Vector Search · Proof of Concept
      </span>
    </div>
  )
}
