import logo from '../assets/codeguard-logo.png';

export default function CodeGuardLogo() {
  return (
    <img
      src={logo}
      alt="CodeGuard AI logo"
      width="48"
      height="48"
      style={{ objectFit: 'contain' }}
    />
  );
}
