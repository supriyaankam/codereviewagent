import {
  createUserWithEmailAndPassword,
  signInWithEmailAndPassword,
  signOut,
  updateProfile,
} from 'firebase/auth';
import { auth } from './firebase';
import { createUserDocument } from './firestoreService';

/**
 * Register a new user, set their display name, and create a Firestore user doc.
 * @param {string} name
 * @param {string} email
 * @param {string} password
 * @returns {Promise<import('firebase/auth').UserCredential>}
 */
export async function registerUser(name, email, password) {
  const credential = await createUserWithEmailAndPassword(auth, email, password);
  const profileWarnings = [];

  try {
    await updateProfile(credential.user, { displayName: name });
  } catch (error) {
    console.warn('[auth] Account created, but display name could not be saved.', error.code);
    profileWarnings.push('Your display name could not be saved.');
  }

  try {
    await createUserDocument(credential.user, { displayName: name });
  } catch (error) {
    console.warn('[auth] Account created, but the Firestore profile could not be saved.', error.code);
    profileWarnings.push('Your user profile could not be saved to Firestore.');
  }

  return { credential, profileWarnings };
}

/**
 * Sign in an existing user.
 * @param {string} email
 * @param {string} password
 * @returns {Promise<import('firebase/auth').UserCredential>}
 */
export async function loginUser(email, password) {
  return signInWithEmailAndPassword(auth, email, password);
}

/**
 * Sign out the current user.
 * @returns {Promise<void>}
 */
export async function logoutUser() {
  return signOut(auth);
}
