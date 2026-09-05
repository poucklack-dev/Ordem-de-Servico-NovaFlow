import React from 'react';import{X}from'lucide-react';
export function Title({title,sub,action}:{title:string;sub:string;action?:React.ReactNode}){return <div className="title"><div><h1>{title}</h1><p>{sub}</p></div>{action}</div>}
export function Loading(){return <div className="skeleton"><i/><i/><i/><i/></div>}
export function Empty({text}:{text:string}){return <div className="empty">{text}</div>}
export function Modal({title,children,onClose}:{title:string;children:React.ReactNode;onClose:()=>void}){return <div className="overlay"><div className="modal form-modal"><button className="close" onClick={onClose}><X/></button><h2>{title}</h2>{children}</div></div>}
export function Notice({message,error=false}:{message:string;error?:boolean}){return message?<div className={error?'notice error':'notice'}>{message}</div>:null}
export const money=(v:number|null|undefined)=>v==null?'—':new Intl.NumberFormat('pt-BR',{style:'currency',currency:'BRL'}).format(v);

