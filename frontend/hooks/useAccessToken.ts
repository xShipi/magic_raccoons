import { useSession } from "next-auth/react";
import { useEffect, useState } from "react";

export default function useAccessToken() {
  const [accessToken, setAccessToken] = useState<string | undefined>(undefined);
  const session = useSession();

  useEffect((() => {
    if (session.data?.accessToken) {
      if (session.data.accessToken !== accessToken) {
        setAccessToken(session.data.accessToken);
      }
    }
  }), [session, accessToken]);

  if (session.data?.accessToken) {
    return session.data?.accessToken;
  }

  return accessToken;
}